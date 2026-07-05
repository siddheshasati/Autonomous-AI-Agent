# Autonomous Document Agent

A small autonomous agent that takes a natural-language business request,
plans its own steps, executes them, checks its own work, and returns a
formatted Word document — exposed as a FastAPI service.

```
POST /agent   {"request": "..."}
  -> plans a document type + section list
  -> drafts every section
  -> self-checks every section and revises if needed
  -> renders a .docx
  -> returns the plan, assumptions, and a download link
```

## Quick start

```bash
pip install -r requirements.txt

# Optional but recommended: free Groq API key -> https://console.groq.com
export GROQ_API_KEY="gsk_..."
# (or, for a local model instead: export OLLAMA_HOST="http://localhost:11434")

uvicorn main:app --reload --port 8000
```

If neither `GROQ_API_KEY` nor `OLLAMA_HOST` is set, the agent automatically
falls back to a deterministic offline generator (`offline_model.py`) so the
whole pipeline — planning, drafting, self-check, docx rendering — still runs
end-to-end with zero network access. This is what the two `test*_*.docx`
files in this folder were generated with, and it's what `GET /health` will
report as `"llm_backend": "offline"`. Point `GROQ_API_KEY` at it and the
exact same code path runs on a real 70B model instead — nothing else
changes.

## Try it

```bash
curl -X POST http://localhost:8000/agent \
  -H "Content-Type: application/json" \
  -d '{"request": "Write a project proposal for migrating our on-prem data warehouse to the cloud"}'

# response includes: {"download_url": "/agent/<id>/download", ...}
curl -o proposal.docx http://localhost:8000/agent/<id>/download
```

## Architecture

```
main.py           FastAPI routes: POST /agent, GET /agent/{id}/download, GET /health
agent.py          AutonomousAgent: validate -> plan -> execute -> self-check -> assemble
llm_client.py      Backend-agnostic LLM wrapper (Groq / Ollama / offline fallback) + retry logic
offline_model.py   Deterministic stand-in used only when no real backend is configured
doc_builder.py     python-docx rendering, isolated from all reasoning/generation logic
```

Each module has exactly one job. `agent.py` never knows or cares whether
`llm_client.py` is talking to Groq, Ollama, or the offline stub — swapping
models means changing an environment variable, not the agent's logic. Same
separation between "decide what to write" (`agent.py`) and "how a .docx is
laid out" (`doc_builder.py`): reformatting the document never touches the
reasoning code, and vice versa.

## Autonomous behaviour

Given only `{"request": "..."}"`, the agent decides, without being told:

- **What kind of document to produce** (proposal, SOP, meeting minutes,
  technical design doc, business report, product spec, ...) based on the
  request's content.
- **What sections that document needs**, each with a stated goal (this is
  the "own TODO list" — visible in the API response's implicit structure
  and used to drive drafting).
- **What to assume** when the request is incomplete or contradictory
  (missing budget, missing owner, missing deadline, conflicting scope) —
  these assumptions are stated explicitly, both in the API response and as
  a dedicated "Assumptions Made by the Agent" section in the .docx, rather
  than silently guessed or blocked on a clarifying question. The agent is
  designed to run unattended, so it always makes a stated, reasonable
  assumption instead of stalling.

## The mandatory engineering improvement: Reflection / Self-Check

**What I implemented:** after each section is drafted, a second, separate
LLM pass (`AutonomousAgent.self_check`) reviews that section against the
goal the planner gave it and returns a verdict: `pass` or `revise` (with a
one-line reason). If the verdict is `revise`, the agent regenerates that
section exactly once, feeding the critique back in as extra guidance, and
accepts the result unconditionally — bounded to one retry, so there's no
risk of an infinite refine loop or runaway cost.

**Why this one, over the other options:** the base assignment already
requires planning ("create its own task/TODO list") and structured
execution, so implementing multi-step planning alone wouldn't add much on
top of the baseline. What was actually missing was any mechanism to catch
the failure mode every LLM-drafted document has — a section that's vague,
generic, or drifts from what it was supposed to cover. Reflection is the
cheapest way to close that gap: it doesn't require external tools or a
retrieval index, and it directly targets output *quality*, which is what a
document-generation agent is ultimately judged on.

**How it improves the agent, concretely:** it turns "an LLM wrote some text"
into "an agent checked its own work before shipping it." In the .docx
output, any section the agent revised is flagged inline
(*"self-corrected by agent QA pass"*) so the reasoning stays visible instead
of silently disappearing — and the API response reports, per section,
whether it passed or was revised, so the behaviour is auditable, not a black
box. You can see this fire directly:

```python
from agent import AutonomousAgent, SectionResult
a = AutonomousAgent()
weak = SectionResult(title="Risks & Mitigations", content="There are some risks.")
result = a.self_check("Launch a new product",
                       {"document_type": "proposal", "title": "x", "assumptions": []},
                       {"heading": "Risks & Mitigations", "goal": "Identify key risks."},
                       weak)
print(result.self_check_verdict)  # "revise"
print(result.revised)             # True
```

### Other engineering hygiene included (not the "chosen" feature, but worth noting)
- **Request validation / guardrails**: empty/oversized requests and crude
  prompt-injection attempts (e.g. "ignore all previous instructions") are
  rejected with `422` before any LLM call is made.
- **Retry & fallback in the LLM client**: transient backend failures retry,
  and if the configured backend keeps failing, the client degrades to the
  offline generator rather than the whole request failing with a 500.
- **Defensive JSON parsing**: models routinely wrap JSON in prose or
  markdown fences despite instructions; `LLMClient.complete_json` strips
  fences and falls back to extracting the widest `{...}` span before giving
  up.

## Two test inputs (see included sample outputs)

1. **Standard business request** — `test1_standard.docx`
   > "Write a project proposal for migrating our on-prem data warehouse to the cloud"

   Straightforward: the agent picks "project proposal", plans 7 sections
   (Executive Summary, Objectives, Scope, Timeline & Milestones, Budget &
   Resources, Risks & Mitigations, Next Steps), and drafts each one.

2. **Complex / ambiguous / conflicting request** — `test2_complex.docx`
   > "We need something for the new AI feature launch, leadership wants it
   > by Friday but also wants it to cover both technical rollout and the
   > go-to-market side, not sure who owns what yet"

   This request never says what kind of document is wanted, gives a
   deadline instead of a timeline, asks for two different audiences
   (technical + GTM) in one document, and explicitly admits ownership is
   undecided. The agent has to *decide* a document type is appropriate,
   fold both technical and GTM concerns into one section structure, and
   surface the missing-ownership problem as a stated assumption rather than
   stalling on it — which is exactly what shows up in its
   "Assumptions Made by the Agent" section.

## Scaling this further

- **Concurrency**: section drafting is currently sequential; since each
  section's prompt is independent, it's a straightforward `asyncio.gather`
  away from being parallelized (the LLM client would need an async
  variant).
- **Persistence**: `generated_documents/` is local disk for this exercise;
  swapping in S3/blob storage behind `doc_builder.build_document`'s return
  value is a one-function change.
- **Observability**: every stage already logs through the standard `logging`
  module with a per-request id; wiring that to structured logging (e.g.
  JSON logs + a request-id header) would make this production-traceable
  with no architectural change.
- **RAG**: if the target documents needed to cite real internal data (past
  proposals, an actual style guide, real org-chart ownership), the natural
  next step is a retrieval step between `plan()` and `draft_section()` that
  pulls relevant context per section — the module boundary already exists
  for it (see `llm_client.py` and `agent.py` separation).
