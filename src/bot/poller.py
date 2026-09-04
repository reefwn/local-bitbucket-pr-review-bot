from datetime import datetime, timedelta

from src.db import is_reviewed


def filter_open(prs: list[dict]) -> list[dict]:
    """Keep only PRs in the OPEN state."""
    return [pr for pr in prs if pr.get("state") == "OPEN"]


def filter_recent(prs: list[dict], now: datetime, window_minutes: int) -> list[dict]:
    """Keep only PRs whose created_on falls within window_minutes of now."""
    cutoff = now - timedelta(minutes=window_minutes)
    recent = []
    for pr in prs:
        created_on = datetime.fromisoformat(pr["created_on"].replace("Z", "+00:00"))
        if created_on >= cutoff:
            recent.append(pr)
    return recent


def filter_unreviewed(prs: list[dict], db_path: str, repo_slug: str) -> list[dict]:
    """Keep only PRs not already recorded as reviewed."""
    return [pr for pr in prs if not is_reviewed(db_path, repo_slug, pr["id"])]
