import asyncio
from datetime import datetime, timezone

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.db import get_provider_order, mark_reviewed
from src.review_runner import run_review


def format_comments(comments_data: dict) -> str:
    return "\n".join(
        f"[{c['user']['display_name']}] {c['content']['raw']}"
        for c in comments_data.get("values", [])
        if c.get("content", {}).get("raw")
    )


async def execute_review(
    config: Config,
    client: BitbucketClient,
    repo_slug: str,
    pr_id: int,
) -> dict:
    """Fetch PR context, run the review CLI chain, persist outcome. Raises on failure."""
    pr_data = await client.get(
        f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}"
    )
    comments_data = await client.get(
        f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
    )
    comments_text = format_comments(comments_data)
    result = await asyncio.to_thread(
        run_review,
        repo_slug,
        pr_id,
        comments_text,
        config.mcp_config_path,
        config.kiro_agent_name,
        config.mcp_url,
        get_provider_order(config.db_path),
        config.cursor_workspace,
        config.cursor_model or None,
    )
    try:
        outcome = await client.get_review_outcome(repo_slug, pr_id)
    except Exception:
        outcome = "unknown"
    mark_reviewed(
        config.db_path,
        repo_slug,
        pr_id,
        datetime.now(timezone.utc).isoformat(),
        pr_data["source"]["commit"]["hash"],
        outcome,
    )
    return {
        "status": "reviewed",
        "repo_slug": repo_slug,
        "pr_id": pr_id,
        "outcome": outcome,
        "claude_result": result,
    }
