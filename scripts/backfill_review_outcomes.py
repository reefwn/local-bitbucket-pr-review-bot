"""One-off backfill: resolve outcome='unknown' rows in reviewed_prs using
Bitbucket's own participants data, for reviews recorded before outcome
tracking existed.

Usage (inside the bot/web container, where DB_PATH and Bitbucket
credentials are available):

    python -m scripts.backfill_review_outcomes
"""
import asyncio
import logging
import sqlite3

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.db import mark_reviewed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def backfill(config: Config) -> None:
    conn = sqlite3.connect(config.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT repo_slug, pr_id, reviewed_at, last_commit_hash FROM reviewed_prs WHERE outcome = 'unknown'"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        logger.info("No rows with outcome='unknown' — nothing to backfill.")
        return

    logger.info("Backfilling outcome for %d review(s)...", len(rows))
    client = BitbucketClient(config)
    try:
        updated = 0
        for row in rows:
            repo_slug, pr_id = row["repo_slug"], row["pr_id"]
            try:
                outcome = await client.get_review_outcome(repo_slug, pr_id)
            except Exception:
                logger.exception("Failed to look up outcome for %s PR #%s — leaving as unknown", repo_slug, pr_id)
                continue
            if outcome == "unknown":
                logger.info("%s PR #%s: still unknown (no matching participant found)", repo_slug, pr_id)
                continue
            mark_reviewed(config.db_path, repo_slug, pr_id, row["reviewed_at"], row["last_commit_hash"], outcome)
            logger.info("%s PR #%s: %s", repo_slug, pr_id, outcome)
            updated += 1
        logger.info("Backfill complete: %d/%d rows updated.", updated, len(rows))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(backfill(Config()))
