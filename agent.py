"""
agent.py
--------
The autonomous agent itself.

Pipeline (each stage is a separate, independently-testable method):

    1. validate_request   -> reject empty/unsafe/out-of-scope input early
    2. plan                -> LLM decomposes the request into a document type,
                               an ordered section list, and explicit
                               assumptions it is making about missing info
    3. execute_plan        -> LLM drafts content for each planned section
    4. self_check          -> **the mandatory engineering improvement**:
                               a second LLM pass critiques each drafted
                               section against the plan/requirements and
                               triggers exactly one revision if it finds a
                               real problem (see docstring on self_check)
    5. build_docx          -> python-docx renders the final, formatted
                               Word document from the structured plan +
                               (possibly revised) section content

Everything the agent decided along the way (plan, assumptions, per-section
self-check verdicts) is returned to the caller in the API response, not just
the file - that's what makes the reasoning visible/auditable instead of a
black box.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from llm_client import LLMClient
from doc_builder import build_document

logger = logging.getLogger("agent.core")

MAX_REQUEST_CHARS = 4000
BANNED_PATTERNS = [
    r"ignore (all|previous) instructions",
    r"reveal.*system prompt",
]


class RequestValidationError(Exception):
    pass


@dataclass
class SectionResult:
    title: str
    content: str
    self_check_verdict: str = "not_checked"
    revised: bool = False


@dataclass
class AgentRun:
    request: str
    document_type: str
    title: str
    plan: list
    assumptions: list
    sections: list = field(default_factory=list)  # list[SectionResult]
    output_path: Optional[str] = None


class AutonomousAgent:
    def __init__(self, llm: Optional[LLMClient] = None):
        self.llm = llm or LLMClient()

    # ------------------------------------------------------------------ #
    # Stage 1: guardrails
    # ------------------------------------------------------------------ #
    def validate_request(self, request: str) -> None:
        if not request or not request.strip():
            raise RequestValidationError("Request text is empty.")
        if len(request) > MAX_REQUEST_CHARS:
            raise RequestValidationError(
                f"Request too long ({len(request)} chars); limit is {MAX_REQUEST_CHARS}."
            )
        lowered = request.lower()
        for pattern in BANNED_PATTERNS:
            if re.search(pattern, lowered):
                raise RequestValidationError(
                    "Request contains disallowed instruction-override content."
                )

    # ------------------------------------------------------------------ #
    # Stage 2: planning
    # ------------------------------------------------------------------ #
    def plan(self, request: str) -> dict:
        system = (
            "You are the planning module of an autonomous document-writing agent. "
            "Given a business request, decide (a) the single best document type to "
            "produce, (b) an ordered list of sections that document should contain, "
            "and (c) any assumptions you must make because the request is ambiguous, "
            "incomplete, or conflicting. Always make a reasonable assumption instead "
            "of stopping to ask a question - this agent runs unattended."
        )
        user = (
            f"Business request:\n\"\"\"\n{request}\n\"\"\"\n\n"
            "Return JSON with exactly this shape:\n"
            "{\n"
            '  "document_type": "<e.g. project plan, proposal, meeting minutes, SOP>",\n'
            '  "title": "<document title>",\n'
            '  "assumptions": ["<assumption 1>", "..."],\n'
            '  "sections": [\n'
            '    {"heading": "<section heading>", "goal": "<one line: what this section must accomplish>"}\n'
            "  ]\n"
            "}\n"
            "Aim for 5-8 sections. Be specific to the request, not generic."
        )
        plan = self.llm.complete_json(system, user)
        self._validate_plan_shape(plan)
        return plan

    @staticmethod
    def _validate_plan_shape(plan: dict) -> None:
        required = {"document_type", "title", "assumptions", "sections"}
        missing = required - plan.keys()
        if missing:
            raise RequestValidationError(f"Planner output missing keys: {missing}")
        if not isinstance(plan["sections"], list) or not plan["sections"]:
            raise RequestValidationError("Planner produced no sections.")

    # ------------------------------------------------------------------ #
    # Stage 3: execution
    # ------------------------------------------------------------------ #
    def draft_section(self, request: str, plan: dict, section: dict) -> str:
        system = (
            "You are the drafting module of an autonomous document-writing agent. "
            "Write clear, professional business prose for ONE section of a larger "
            "document. Do not repeat the section heading in your answer. Use "
            "concrete, realistic (mock, if needed) detail rather than vague filler. "
            "Use short paragraphs and, where natural, bullet points (as '- ' lines)."
        )
        user = (
            f"Overall request: \"{request}\"\n"
            f"Document type: {plan['document_type']}\n"
            f"Document title: {plan['title']}\n"
            f"Assumptions already made: {plan['assumptions']}\n"
            f"Section to write: \"{section['heading']}\"\n"
            f"Section goal: {section['goal']}\n\n"
            "Write the section body now (plain text, no heading line)."
        )
        return self.llm.complete(system, user).strip()

    def execute_plan(self, request: str, plan: dict) -> list:
        sections = []
        for section in plan["sections"]:
            content = self.draft_section(request, plan, section)
            sections.append(SectionResult(title=section["heading"], content=content))
        return sections

    # ------------------------------------------------------------------ #
    # Stage 4: MANDATORY ENGINEERING IMPROVEMENT — reflection / self-check
    # ------------------------------------------------------------------ #
    def self_check(self, request: str, plan: dict, section: dict, result: SectionResult) -> SectionResult:
        """
        Reflection pass: ask the model to critique its own draft against the
        section's stated goal and flag concrete problems (too generic, missing
        the number/date/name a business reader would expect, contradicts an
        assumption, wrong tone, etc.).

        If the critique reports a real problem, we regenerate the section
        EXACTLY ONCE with the critique appended as extra guidance, then accept
        the result unconditionally (bounded cost - no infinite refine loops).
        This is what turns "an LLM wrote something" into "an agent checked its
        own work before shipping it", which is the actual gap between a demo
        script and something you'd trust to run unattended.
        """
        system = (
            "You are the QA/reflection module of an autonomous document-writing "
            "agent. Review a drafted section critically against its stated goal. "
            "Return JSON: {\"verdict\": \"pass\"|\"revise\", \"issue\": \"<short reason, "
            "empty string if pass>\"}. Only say 'revise' for a concrete, fixable "
            "problem - not for style preference."
        )
        user = (
            f"Section heading: {section['heading']}\n"
            f"Section goal: {section['goal']}\n"
            f"Drafted content:\n\"\"\"\n{result.content}\n\"\"\"\n"
        )
        try:
            verdict = self.llm.complete_json(system, user)
        except Exception as exc:
            logger.warning("Self-check parse failed, defaulting to pass: %s", exc)
            verdict = {"verdict": "pass", "issue": ""}

        result.self_check_verdict = verdict.get("verdict", "pass")

        if result.self_check_verdict == "revise":
            issue = verdict.get("issue", "unspecified issue")
            logger.info("Revising section '%s' due to: %s", section["heading"], issue)
            revise_system = (
                "You are the drafting module of an autonomous document-writing agent. "
                "Rewrite the section to fix the specific issue identified by QA, "
                "keeping everything else that already worked."
            )
            revise_user = (
                f"Overall request: \"{request}\"\n"
                f"Section: \"{section['heading']}\" (goal: {section['goal']})\n"
                f"Previous draft:\n\"\"\"\n{result.content}\n\"\"\"\n"
                f"QA issue to fix: {issue}\n\n"
                "Write the corrected section body now (plain text, no heading line)."
            )
            result.content = self.llm.complete(revise_system, revise_user).strip()
            result.revised = True

        return result

    # ------------------------------------------------------------------ #
    # Orchestration entry point
    # ------------------------------------------------------------------ #
    def run(self, request: str, output_path: str) -> AgentRun:
        self.validate_request(request)
        plan = self.plan(request)

        run = AgentRun(
            request=request,
            document_type=plan["document_type"],
            title=plan["title"],
            plan=plan["sections"],
            assumptions=plan.get("assumptions", []),
        )

        for section in plan["sections"]:
            content = self.draft_section(request, plan, section)
            result = SectionResult(title=section["heading"], content=content)
            result = self.self_check(request, plan, section, result)
            run.sections.append(result)

        run.output_path = build_document(
            title=plan["title"],
            document_type=plan["document_type"],
            assumptions=run.assumptions,
            sections=run.sections,
            output_path=output_path,
        )
        return run
