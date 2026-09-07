from datetime import datetime, timedelta

from src.db import is_reviewed


def filter_open(prs: list[dict]) -> list[dict]:
    """Keep only PRs in the OPEN state."""
    return [pr for pr in prs if pr.get("state") == "OPEN"]


def filter_recent(prs: list[dict], now: datetime, window_minutes: int) -> list[dict]:
    """Keep only PRs created or last updated within window_minutes of now.

    Using updated_on (not just created_on) means a PR that gets new commits
    pushed re-enters the window so it can be picked up for re-review.
    """
    cutoff = now - timedelta(minutes=window_minutes)
    recent = []
    for pr in prs:
        created_on = datetime.fromisoformat(pr["created_on"].replace("Z", "+00:00"))
        updated_on_raw = pr.get("updated_on", pr["created_on"])
        updated_on = datetime.fromisoformat(updated_on_raw.replace("Z", "+00:00"))
        if created_on >= cutoff or updated_on >= cutoff:
            recent.append(pr)
    return recent


def filter_unreviewed(prs: list[dict], db_path: str, repo_slug: str) -> list[dict]:
    """Keep only PRs not already reviewed at their current source commit hash."""
    return [
        pr
        for pr in prs
        if not is_reviewed(db_path, repo_slug, pr["id"], pr["source"]["commit"]["hash"])
    ]
