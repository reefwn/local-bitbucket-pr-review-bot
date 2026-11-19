from datetime import datetime, timezone

from fastapi import HTTPException

from src.config import Config
from src.pr_url import extract_pr_urls_from_text
from src.web.review_queue import ReviewQueue


def enqueue_review_request(
    review_queue: ReviewQueue,
    config: Config,
    *,
    pr_urls: list[str],
    text: str = "",
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    urls: list[str] = []
    seen: set[str] = set()
    rejected: list[dict] = []

    for raw in pr_urls:
        url = raw.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)

    found, parse_errors = extract_pr_urls_from_text(text, config.bitbucket_workspace)
    for err in parse_errors:
        rejected.append({"input": "", "error": err})
    for url in found:
        if url not in seen:
            seen.add(url)
            urls.append(url)

    if not urls and not rejected:
        raise HTTPException(status_code=400, detail="No PR URLs provided.")

    added, enqueue_rejected = review_queue.enqueue_urls(urls, now)
    rejected.extend(enqueue_rejected)
    return {
        "enqueued": [item.to_dict() for item in added],
        "rejected": rejected,
    }