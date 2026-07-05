"""
main.py
-------
FastAPI surface for the autonomous document agent.

    POST /agent   {"request": "..."}   -> plan, execute, self-check, render docx
    GET  /health  -> liveness + which LLM backend is active

Run:
    uvicorn main:app --reload --port 8000

Try it:
    curl -X POST http://localhost:8000/agent \
         -H "Content-Type: application/json" \
         -d '{"request": "Write meeting minutes for our weekly engineering sync"}'
"""
from __future__ import annotations

import logging
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agent import AutonomousAgent, RequestValidationError
from llm_client import LLMClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("agent.api")

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "generated_documents")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

app = FastAPI(
    title="Autonomous Document Agent",
    description="Accepts a natural-language business request and returns a "
                 "generated Word document plus the agent's plan/reasoning.",
    version="1.0.0",
)

_llm = LLMClient()
_agent = AutonomousAgent(llm=_llm)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class AgentRequest(BaseModel):
    request: str = Field(..., description="Natural language description of the document needed")


class SectionOut(BaseModel):
    heading: str
    self_check: str
    revised: bool


class AgentResponse(BaseModel):
    message: str
    document_type: str
    title_used: str
    assumptions: list[str]
    sections: list[SectionOut]
    download_url: str
    llm_backend: str


@app.get("/health")
def health():
    return {"status": "ok", "llm_backend": _llm.backend}


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.post("/agent", response_model=AgentResponse)
def run_agent(payload: AgentRequest):
    request_id = uuid.uuid4().hex[:8]
    output_path = os.path.join(OUTPUT_DIR, f"document_{request_id}.docx")

    try:
        run = _agent.run(payload.request, output_path)
    except RequestValidationError as exc:
        logger.info("Rejected request: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # unexpected failure anywhere in the pipeline
        logger.exception("Agent run failed unexpectedly")
        raise HTTPException(status_code=500, detail=f"Agent failed: {exc}")

    sections_out = [
        SectionOut(
            heading=s.title,
            self_check=s.self_check_verdict,
            revised=s.revised,
        )
        for s in run.sections
    ]

    return AgentResponse(
        message=(
            f"Generated a {run.document_type} with {len(run.sections)} sections. "
            f"{sum(1 for s in run.sections if s.revised)} section(s) were revised "
            f"after the agent's self-check pass."
        ),
        document_type=run.document_type,
        title_used=run.title,
        assumptions=run.assumptions,
        sections=sections_out,
        download_url=f"/agent/{request_id}/download",
        llm_backend=_llm.backend,
    )


@app.get("/agent/{request_id}/download")
def download(request_id: str):
    path = os.path.join(OUTPUT_DIR, f"document_{request_id}.docx")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Document not found (may have expired or invalid id).")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=os.path.basename(path),
    )
