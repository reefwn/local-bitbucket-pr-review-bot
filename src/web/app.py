import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.db import count_reviews, init_db, list_recent_reviews, mark_reviewed
from src.pr_url import parse_pr_url
from src.review_runner import run_review, write_kiro_mcp_config, write_mcp_config

_FORM_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PR Review</title>
<style>
  :root {
    --bg: #0d1117;
    --bg-raised: #11161d;
    --border: #21262d;
    --text: #c9d1d9;
    --text-dim: #6e7681;
    --accent: #58a6ff;
    --ok: #3fb950;
    --err: #f85149;
    --pending: #d29922;
    --mono: ui-monospace, "SF Mono", "Menlo", "Cascadia Code", monospace;
  }

  * { box-sizing: border-box; }

  @media (prefers-reduced-motion: reduce) {
    * { animation: none !important; transition: none !important; }
  }

  html, body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--mono);
    font-size: 14px;
    line-height: 1.6;
  }

  main {
    max-width: 640px;
    margin: 0 auto;
    padding: 48px 20px 80px;
  }

  h1 {
    font-size: 15px;
    font-weight: 600;
    color: var(--text);
    margin: 0 0 4px;
  }

  h1::before {
    content: "$ ";
    color: var(--text-dim);
  }

  .sub {
    color: var(--text-dim);
    font-size: 13px;
    margin: 0 0 32px;
  }

  form {
    display: flex;
    gap: 8px;
    margin-bottom: 8px;
  }

  input[type="text"] {
    flex: 1;
    background: var(--bg-raised);
    border: 1px solid var(--border);
    color: var(--text);
    font-family: var(--mono);
    font-size: 13px;
    padding: 10px 12px;
    border-radius: 4px;
    outline: none;
    min-width: 0;
  }

  input[type="text"]::placeholder {
    color: var(--text-dim);
  }

  input[type="text"]:focus {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px var(--accent);
  }

  button {
    background: var(--accent);
    color: #0d1117;
    border: none;
    font-family: var(--mono);
    font-size: 13px;
    font-weight: 600;
    padding: 10px 18px;
    border-radius: 4px;
    cursor: pointer;
    white-space: nowrap;
  }

  button:hover { filter: brightness(1.1); }
  button:active { filter: brightness(0.95); }
  button:disabled { opacity: 0.5; cursor: default; }
  button:focus-visible, input:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }

  .status-line {
    min-height: 20px;
    font-size: 13px;
    color: var(--text-dim);
    margin-bottom: 56px;
  }
  .status-line.err { color: var(--err); }
  .status-line.ok { color: var(--ok); }

  @media (max-width: 480px) {
    form { flex-direction: column; }
    button { align-self: flex-start; }
  }

  .log-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border);
  }

  .log-header h2 {
    font-size: 13px;
    font-weight: 600;
    color: var(--text-dim);
    margin: 0;
  }

  .log-header .count {
    font-size: 12px;
    color: var(--text-dim);
  }

  #log {
    display: flex;
    flex-direction: column;
  }

  .row {
    display: grid;
    grid-template-columns: 14px 1fr auto;
    gap: 10px;
    align-items: baseline;
    padding: 7px 0;
    border-bottom: 1px solid var(--border);
    font-size: 13px;
  }

  .row:last-child { border-bottom: none; }

  .row .glyph { color: var(--ok); }
  .row.pending .glyph { color: var(--pending); }

  .row .path {
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .row .path .pr-id { color: var(--text-dim); }
  .row .path a {
    color: inherit;
    text-decoration: none;
  }
  .row .path a:hover { color: var(--accent); }
  .row .path a:hover .pr-id { color: var(--accent); }
  .row .path a:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
    border-radius: 2px;
  }

  .row .time {
    color: var(--text-dim);
    font-size: 12px;
  }

  .empty {
    color: var(--text-dim);
    font-size: 13px;
    padding: 20px 0;
  }

  .pager {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: 16px;
    font-size: 12px;
    color: var(--text-dim);
  }

  .pager[hidden] { display: none; }

  .pager button {
    background: transparent;
    color: var(--text-dim);
    border: 1px solid var(--border);
    font-size: 12px;
    font-weight: 400;
    padding: 5px 10px;
  }

  .pager button:hover:not(:disabled) {
    color: var(--text);
    border-color: var(--text-dim);
    filter: none;
  }

  .pager button:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .fade-in {
    animation: fadeIn 0.35s ease-out;
  }
  @keyframes fadeIn {
    from { opacity: 0; }
    to { opacity: 1; }
  }
</style>
</head>
<body>
<main>
  <h1>pr-review-bot</h1>
  <p class="sub">Paste a Bitbucket pull request URL to review it now.</p>

  <form id="review-form">
    <input
      type="text"
      id="pr_url"
      name="pr_url"
      autocomplete="off"
      spellcheck="false"
      placeholder="https://bitbucket.org/{workspace}/{repo}/pull-requests/{id}"
    >
    <button type="submit" id="submit-btn">Review</button>
  </form>
  <p class="status-line" id="status"></p>

  <div class="log-header">
    <h2>recently reviewed</h2>
    <span class="count" id="log-count"></span>
  </div>
  <div id="log"><p class="empty">Loading&hellip;</p></div>
  <div class="pager" id="pager" hidden>
    <button type="button" id="prev-page">&larr; Newer</button>
    <span id="pager-label"></span>
    <button type="button" id="next-page">Older &rarr;</button>
  </div>
</main>

<script>
const statusEl = document.getElementById("status");
const logEl = document.getElementById("log");
const logCountEl = document.getElementById("log-count");
const form = document.getElementById("review-form");
const submitBtn = document.getElementById("submit-btn");
const urlInput = document.getElementById("pr_url");
const pagerEl = document.getElementById("pager");
const pagerLabelEl = document.getElementById("pager-label");
const prevPageBtn = document.getElementById("prev-page");
const nextPageBtn = document.getElementById("next-page");

const PER_PAGE = 10;
let currentPage = 1;

function repoAndPr(repo_slug, pr_id) {
  return `${repo_slug} <span class="pr-id">#${pr_id}</span>`;
}

function formatTime(iso) {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${d.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
}

function renderLog(data) {
  const reviews = data.reviews || [];
  logCountEl.textContent = data.total ? `${data.total}` : "";

  if (!reviews.length) {
    logEl.innerHTML = currentPage === 1
      ? '<p class="empty">No PRs reviewed yet. They will appear here once the bot runs.</p>'
      : '<p class="empty">No more reviews.</p>';
  } else {
    logEl.innerHTML = reviews.map(r => `
      <div class="row">
        <span class="glyph">&#10003;</span>
        <span class="path">
          <a href="${r.pr_url}" target="_blank" rel="noopener noreferrer">${repoAndPr(r.repo_slug, r.pr_id)}</a>
        </span>
        <span class="time">${formatTime(r.reviewed_at)}</span>
      </div>
    `).join("");
  }

  const totalPages = data.total_pages || 1;
  pagerEl.hidden = totalPages <= 1;
  pagerLabelEl.textContent = `page ${data.page || 1} of ${totalPages}`;
  prevPageBtn.disabled = (data.page || 1) <= 1;
  nextPageBtn.disabled = (data.page || 1) >= totalPages;
}

async function loadReviews(page = currentPage) {
  try {
    const res = await fetch(`/reviews?page=${page}&per_page=${PER_PAGE}`);
    const data = await res.json();
    currentPage = data.page || page;
    renderLog(data);
  } catch {
    logEl.innerHTML = '<p class="empty">Could not load review history.</p>';
  }
}

prevPageBtn.addEventListener("click", () => {
  if (currentPage > 1) loadReviews(currentPage - 1);
});
nextPageBtn.addEventListener("click", () => {
  loadReviews(currentPage + 1);
});

function prependPending(repoGuess) {
  const row = document.createElement("div");
  row.className = "row pending fade-in";
  row.innerHTML = `
    <span class="glyph">&#8230;</span>
    <span class="path">${repoGuess || "reviewing&hellip;"}</span>
    <span class="time">now</span>
  `;
  const empty = logEl.querySelector(".empty");
  if (empty) empty.remove();
  logEl.prepend(row);
  return row;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const pr_url = urlInput.value.trim();
  if (!pr_url) return;

  submitBtn.disabled = true;
  statusEl.textContent = "Reviewing\u2026";
  statusEl.className = "status-line";
  const pendingRow = currentPage === 1 ? prependPending(pr_url.replace(/^https?:\\/\\//, "")) : null;

  try {
    const res = await fetch("/review", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pr_url }),
    });
    const data = await res.json();
    if (pendingRow) pendingRow.remove();

    if (res.ok) {
      statusEl.textContent = `Reviewed ${data.repo_slug} #${data.pr_id}`;
      statusEl.className = "status-line ok";
      urlInput.value = "";
    } else {
      statusEl.textContent = data.detail || "Review failed.";
      statusEl.className = "status-line err";
    }
  } catch {
    if (pendingRow) pendingRow.remove();
    statusEl.textContent = "Review failed: could not reach the server.";
    statusEl.className = "status-line err";
  } finally {
    submitBtn.disabled = false;
    loadReviews(1);
  }
});

loadReviews();
setInterval(() => {
  if (currentPage === 1) loadReviews(1);
}, 15000);
</script>
</body>
</html>
"""


class ReviewRequest(BaseModel):
    pr_url: str


def _format_comments(comments_data: dict) -> str:
    return "\n".join(
        f"[{c['user']['display_name']}] {c['content']['raw']}"
        for c in comments_data.get("values", [])
        if c.get("content", {}).get("raw")
    )


def create_app(config: Config, client: BitbucketClient | None = None) -> FastAPI:
    """Build the FastAPI app, wiring in the given Config (and optionally an injected client for tests)."""
    init_db(config.db_path)
    write_mcp_config(config.mcp_config_path, config.mcp_url)
    write_kiro_mcp_config(config.kiro_mcp_config_path, config.mcp_url)
    client = client or BitbucketClient(config)
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _FORM_HTML

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.get("/reviews")
    async def reviews(page: int = 1, per_page: int = 10) -> dict:
        page = max(page, 1)
        per_page = min(max(per_page, 1), 100)
        offset = (page - 1) * per_page
        rows = list_recent_reviews(config.db_path, limit=per_page, offset=offset)
        for row in rows:
            row["pr_url"] = (
                f"https://bitbucket.org/{config.bitbucket_workspace}/{row['repo_slug']}"
                f"/pull-requests/{row['pr_id']}"
            )
        total = count_reviews(config.db_path)
        return {
            "reviews": rows,
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": max((total + per_page - 1) // per_page, 1),
        }

    @app.post("/review")
    async def review(req: ReviewRequest) -> dict:
        try:
            repo_slug, pr_id = parse_pr_url(req.pr_url, config.bitbucket_workspace)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            pr_data = await client.get(f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}")
            comments_data = await client.get(
                f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
            )
            comments_text = _format_comments(comments_data)
            result = await asyncio.to_thread(
                run_review, repo_slug, pr_id, comments_text, config.mcp_config_path, config.kiro_agent_name
            )
            mark_reviewed(
                config.db_path,
                repo_slug,
                pr_id,
                datetime.now(timezone.utc).isoformat(),
                pr_data["source"]["commit"]["hash"],
            )
            return {"status": "reviewed", "repo_slug": repo_slug, "pr_id": pr_id, "claude_result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Review failed: {e}")

    return app


def app_factory() -> FastAPI:
    return create_app(Config())
