import asyncio
import logging
from datetime import datetime, timezone

from src.bitbucket_client import BitbucketClient
from src.bot.poller import filter_open, filter_recent, filter_unreviewed
from src.config import Config
from src.db import init_db, mark_reviewed
from src.review_runner import run_review, write_kiro_mcp_config, write_mcp_config

logger = logging.getLogger(__name__)


async def resolve_repo_slugs(client: BitbucketClient, workspace: str, project_keys: list[str]) -> list[str]:
    """Resolve Bitbucket Project keys into member repo slugs."""
    slugs: list[str] = []
    for key in project_keys:
        try:
            repos = await client.get_all_pages(
                f"/repositories/{workspace}", params={"q": f'project.key="{key}"', "pagelen": 100}
            )
            slugs.extend(repo["slug"] for repo in repos)
        except Exception:
            logger.exception("Failed to resolve repos for project key %s", key)
    return list(dict.fromkeys(slugs))


def _format_comments(comments_data: dict) -> str:
    return "\n".join(
        f"[{c['user']['display_name']}] {c['content']['raw']}"
        for c in comments_data.get("values", [])
        if c.get("content", {}).get("raw")
    )


async def _process_repo(config: Config, client: BitbucketClient, repo_slug: str, now: datetime) -> None:
    try:
        data = await client.get(
            f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests",
            params={"state": "OPEN", "pagelen": 50},
        )
        prs = filter_open(data.get("values", []))
        prs = filter_recent(prs, now, config.poll_interval_minutes * 2)
        prs = filter_unreviewed(prs, config.db_path, repo_slug)
        for pr in prs:
            try:
                comments_data = await client.get(
                    f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr['id']}/comments"
                )
                comments_text = _format_comments(comments_data)
                await asyncio.to_thread(
                    run_review,
                    repo_slug,
                    pr["id"],
                    comments_text,
                    config.mcp_config_path,
                    config.kiro_agent_name,
                )
                try:
                    outcome = await client.get_review_outcome(repo_slug, pr["id"])
                except Exception:
                    logger.exception("Failed to determine review outcome for %s PR #%s", repo_slug, pr["id"])
                    outcome = "unknown"
                mark_reviewed(
                    config.db_path,
                    repo_slug,
                    pr["id"],
                    now.isoformat(),
                    pr["source"]["commit"]["hash"],
                    outcome,
                )
            except Exception:
                logger.exception("Review failed for %s PR #%s", repo_slug, pr["id"])
    except Exception:
        logger.exception("Failed to process repo %s", repo_slug)


async def run_cycle(config: Config, client: BitbucketClient) -> None:
    """Run one poll cycle: find newly-opened PRs across configured projects and review them."""
    now = datetime.now(timezone.utc)
    repo_slugs = await resolve_repo_slugs(client, config.bitbucket_workspace, config.project_keys)
    await asyncio.gather(*(_process_repo(config, client, repo_slug, now) for repo_slug in repo_slugs))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config()
    init_db(config.db_path)
    write_mcp_config(config.mcp_config_path, config.mcp_url)
    write_kiro_mcp_config(config.kiro_mcp_config_path, config.mcp_url)
    client = BitbucketClient(config)
    try:
        while True:
            try:
                await run_cycle(config, client)
            except Exception:
                logger.exception("Poll cycle failed")
            await asyncio.sleep(config.poll_interval_minutes * 60)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
