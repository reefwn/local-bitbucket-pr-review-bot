import asyncio
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.db import init_db, mark_reviewed
from src.pr_url import parse_pr_url
from src.review_runner import run_review, write_mcp_config

_FORM_HTML = """<!doctype html>
<html>
<head><title>Bitbucket PR Review</title></head>
<body>
<h1>Review a Bitbucket PR</h1>
<form id="review-form">
  <input type="text" id="pr_url" name="pr_url"
         placeholder="https://bitbucket.org/{workspace}/{repo}/pull-requests/{id}" size="80">
  <button type="submit">Review</button>
</form>
<pre id="result"></pre>
<script>
document.getElementById("review-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pr_url = document.getElementById("pr_url").value;
  document.getElementById("result").textContent = "Reviewing...";
  const res = await fetch("/review", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pr_url}),
  });
  const data = await res.json();
  document.getElementById("result").textContent = JSON.stringify(data, null, 2);
});
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
    client = client or BitbucketClient(config)
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _FORM_HTML

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/review")
    async def review(req: ReviewRequest) -> dict:
        try:
            repo_slug, pr_id = parse_pr_url(req.pr_url, config.bitbucket_workspace)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        try:
            comments_data = await client.get(
                f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
            )
            comments_text = _format_comments(comments_data)
            result = await asyncio.to_thread(run_review, repo_slug, pr_id, comments_text, config.mcp_config_path)
            mark_reviewed(config.db_path, repo_slug, pr_id, datetime.now(timezone.utc).isoformat())
            return {"status": "reviewed", "repo_slug": repo_slug, "pr_id": pr_id, "claude_result": result}
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Review failed: {e}")

    return app


def app_factory() -> FastAPI:
    return create_app(Config())
