# Autonomous Document Agent

Python FastAPI service that accepts a natural-language business request,
creates its own document plan, drafts each section, self-checks the output,
and returns a polished Microsoft Word `.docx` document.

The project is intentionally code-first. The small HTML/CSS/JS page is only a
basic convenience UI; the main assignment surface is the API.

## What It Builds

```text
POST /agent {"request": "..."}
  -> validate the request
  -> decide the document type
  -> create a section-by-section task plan
  -> draft every section
  -> run reflection/self-check
  -> render a .docx
  -> return metadata plus a download URL
```

Supported document styles include proposals, meeting summaries, launch plans,
SOPs, product specs, technical designs, and business reports. Mock details are
used where the request does not provide enough real data.

## Quick Start

```powershell
python -m pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

Open the simple UI:

```text
http://127.0.0.1:8000/
```

Check the backend:

```text
http://127.0.0.1:8000/health
```

## API Example

```powershell
$body = @{
  request = "Write a project proposal for migrating our on-prem data warehouse to the cloud"
} | ConvertTo-Json

Invoke-WebRequest `
  -Uri http://127.0.0.1:8000/agent `
  -Method POST `
  -Body $body `
  -ContentType "application/json"
```

The response includes:

- `document_type`
- `title_used`
- `assumptions`
- `sections`
- `download_url`
- `llm_backend`

Download the generated Word file from:

```text
http://127.0.0.1:8000/agent/<request_id>/download
```

## API Keys And Model Configuration

Keep real API keys in a local `.env` file in the project root. Do not commit
`.env`; it is ignored by `.gitignore`.

Start by copying:

```powershell
Copy-Item .env.example .env
```

For Groq:

```text
GROQ_API_KEY=gsk_your_real_key_here
GROQ_MODEL=llama-3.3-70b-versatile
```

For Ollama:

```text
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=llama3.1
```

Model priority:

1. Groq if `GROQ_API_KEY` is set.
2. Ollama if `OLLAMA_HOST` is set.
3. Offline deterministic fallback if neither is set.

The offline fallback means the full pipeline still runs without paid credits
or internet access.

## Test The Assignment Cases

```powershell
python smoke_test.py
```

The smoke test checks:

1. Standard business request: cloud migration project proposal.
2. Complex ambiguous request: AI feature launch covering technical rollout,
   go-to-market work, deadline pressure, and unclear ownership.
3. Meeting-notes summary: Key Decisions, Action Items, and Next Steps.

It verifies the API response, autonomous section plan, and generated `.docx`
download.

## Architecture

```text
main.py            FastAPI routes and response models
agent.py           Autonomous planning, drafting, self-check orchestration
llm_client.py      Groq / Ollama / offline LLM abstraction
offline_model.py   deterministic no-key fallback for demos and tests
doc_builder.py     Word document rendering with python-docx
static/            minimal HTML/CSS/JS UI
smoke_test.py      end-to-end assignment smoke checks
```

The code keeps model access, agent orchestration, and Word rendering separate.
That makes it easy to change the model, document formatting, or planning logic
without rewriting the whole project.

## Mandatory Engineering Improvement

Chosen improvement: Reflection / self-check.

After drafting each section, the agent runs a second review pass. The reviewer
checks whether the section satisfies the planned goal and returns either
`pass` or `revise`. If revision is needed, the agent regenerates that section
once using the critique.

Why this choice:

- The assignment already requires planning and execution, so output quality is
  the next important failure point.
- Reflection catches vague, incomplete, or off-goal sections before the final
  `.docx` is produced.
- The revision loop is bounded to one retry, so it improves quality without
  creating runaway cost or infinite loops.

Additional engineering hygiene:

- Request validation rejects empty, oversized, and obvious prompt-injection
  style requests.
- LLM retries and fallback prevent a temporary Groq/Ollama failure from
  breaking the whole API call.
- Defensive JSON parsing handles common model formatting mistakes.

## Why This Meets The Brief

- Python API with FastAPI.
- `POST /agent` accepts `{"request": "..."}`.
- The agent decides document type and section plan by itself.
- The agent executes the plan section by section.
- The final output is a generated Microsoft Word `.docx`.
- Uses free/local model options: Groq, Ollama, or offline fallback.
- Demonstrates one mandatory improvement: reflection/self-check.
- Includes standard and complex test inputs.
- Keeps the UI simple and avoids no-code managed platforms.
