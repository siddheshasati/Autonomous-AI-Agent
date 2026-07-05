const form = document.querySelector("#agentForm");
const textarea = document.querySelector("#requestText");
const submitBtn = document.querySelector("#submitBtn");
const clearBtn = document.querySelector("#clearBtn");
const statusEl = document.querySelector("#status");
const backendTag = document.querySelector("#backendTag");
const emptyState = document.querySelector("#emptyState");
const resultEl = document.querySelector("#result");

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setBackend(backend) {
  const label = backend || "unknown";
  backendTag.textContent = `Model: ${label}`;
  statusEl.textContent = `Ready (${label})`;
}

async function loadHealth() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error("Health check failed");
    const data = await response.json();
    setBackend(data.llm_backend);
  } catch (error) {
    statusEl.textContent = "API not ready";
  }
}

function renderResult(data) {
  const sectionCount = data.sections?.length || 0;
  const assumptionCount = data.assumptions?.length || 0;
  const revisedCount = data.sections?.filter((section) => section.revised).length || 0;
  resultEl.innerHTML = `
    <div class="result-block">
      <div class="summary">${escapeHtml(data.message)}</div>
      <div class="meta-grid">
        <div class="meta-item">
          <span class="meta-label">Document type</span>
          <span class="meta-value">${escapeHtml(data.document_type)}</span>
        </div>
        <div class="meta-item">
          <span class="meta-label">Title</span>
          <span class="meta-value">${escapeHtml(data.title_used)}</span>
        </div>
      </div>
      <div class="summary muted-summary">
        Plan: ${sectionCount} section(s), ${assumptionCount} assumption(s), ${revisedCount} self-check revision(s).
      </div>
      <a class="download" href="${escapeHtml(data.download_url)}">Download Word document</a>
    </div>
  `;
  resultEl.classList.remove("hidden");
  emptyState.classList.add("hidden");
  setBackend(data.llm_backend);
}

function renderError(message) {
  resultEl.innerHTML = `<div class="summary error">${escapeHtml(message)}</div>`;
  resultEl.classList.remove("hidden");
  emptyState.classList.add("hidden");
}

clearBtn.addEventListener("click", () => {
  textarea.value = "";
  textarea.focus();
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  submitBtn.disabled = true;
  submitBtn.textContent = "Generating...";
  statusEl.textContent = "Running agent...";

  try {
    const response = await fetch("/agent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: textarea.value }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || "Request failed");
    }
    renderResult(data);
  } catch (error) {
    renderError(error.message);
    statusEl.textContent = "Request failed";
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Generate document";
  }
});

loadHealth();
