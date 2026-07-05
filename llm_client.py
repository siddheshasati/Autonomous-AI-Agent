from __future__ import annotations

import json
import os
import re
import logging
from typing import Optional

logger = logging.getLogger("agent.llm")

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dependency is optional at runtime
    load_dotenv = None
else:
    load_dotenv()


class LLMBackendError(Exception):
    """Raised when a backend call fails after retries are exhausted."""


class LLMClient:
    def __init__(self) -> None:
        self.backend = "offline"
        self._groq_client = None
        self._model = None

        groq_key = os.environ.get("GROQ_API_KEY")
        ollama_host = os.environ.get("OLLAMA_HOST")

        if groq_key:
            try:
                from groq import Groq  # imported lazily, only if needed
                self._groq_client = Groq(api_key=groq_key)
                self._model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
                self.backend = "groq"
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Groq client init failed (%s); falling back", exc)

        elif ollama_host:
            self.backend = "ollama"
            self._model = os.environ.get("OLLAMA_MODEL", "llama3.1")
            self._ollama_host = ollama_host

        logger.info("LLMClient initialised with backend=%s", self.backend)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def complete(self, system: str, user: str, retries: int = 2) -> str:
        """Return raw text completion, with simple retry/fallback logic."""
        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                if self.backend == "groq":
                    return self._complete_groq(system, user)
                if self.backend == "ollama":
                    return self._complete_ollama(system, user)
                return self._complete_offline(system, user)
            except Exception as exc:  # network hiccup, rate limit, etc.
                last_exc = exc
                logger.warning(
                    "LLM call failed (backend=%s, attempt=%d/%d): %s",
                    self.backend, attempt, retries, exc,
                )
        # Retry & fallback: if the configured backend keeps failing, degrade
        # gracefully to the offline generator rather than crashing the request.
        logger.error("All %d attempts failed on backend=%s; using offline fallback. Last error: %s",
                     retries, self.backend, last_exc)
        return self._complete_offline(system, user)

    def complete_json(self, system: str, user: str) -> dict:
        """Ask the model for JSON and parse it defensively (models often
        wrap JSON in prose or markdown fences despite instructions)."""
        raw = self.complete(
            system + "\nRespond with ONLY valid JSON. No prose, no markdown fences.",
            user,
        )
        return self._extract_json(raw)

    # ------------------------------------------------------------------ #
    # Backends
    # ------------------------------------------------------------------ #
    def _complete_groq(self, system: str, user: str) -> str:
        resp = self._groq_client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            max_tokens=2000,
        )
        return resp.choices[0].message.content

    def _complete_ollama(self, system: str, user: str) -> str:
        import httpx
        r = httpx.post(
            f"{self._ollama_host.rstrip('/')}/api/chat",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["message"]["content"]

    def _complete_offline(self, system: str, user: str) -> str:
        """
        Deterministic offline generator. Not an LLM - a rule-based stand-in
        that mimics the *shape* of the response the real prompts ask for, so
        the whole pipeline (planning -> drafting -> self-check -> docx) is
        exercisable with zero external dependencies. Swap in a real
        GROQ_API_KEY and this path is never hit.
        """
        from offline_model import generate_offline_response
        return generate_offline_response(system, user)

    @staticmethod
    def _extract_json(raw: str) -> dict:
        raw = raw.strip()
        # Strip markdown fences if the model added them anyway.
        raw = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Last resort: grab the widest {...} span.
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise LLMBackendError(f"Could not parse JSON from model output: {exc}") from exc
        raise LLMBackendError("Model output contained no JSON object")
