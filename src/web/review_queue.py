import asyncio
import uuid
from dataclasses import asdict, dataclass, field
from typing import Literal

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.pr_url import parse_pr_url
from src.web.review_service import execute_review

QueueStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class QueueItem:
    id: str
    pr_url: str
    repo_slug: str
    pr_id: int
    status: QueueStatus
    error: str | None = None
    outcome: str | None = None
    enqueued_at: str = ""
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReviewQueue:
    config: Config
    client: BitbucketClient
    _items: list[QueueItem] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _wake: asyncio.Event = field(default_factory=asyncio.Event)
    _worker_task: asyncio.Task | None = None

    def _key(self, repo_slug: str, pr_id: int) -> tuple[str, int]:
        return (repo_slug, pr_id)

    def _active_keys(self) -> set[tuple[str, int]]:
        return {
            self._key(item.repo_slug, item.pr_id)
            for item in self._items
            if item.status in ("pending", "running")
        }

    def enqueue_urls(self, pr_urls: list[str], enqueued_at: str) -> tuple[list[QueueItem], list[dict]]:
        """Parse and append new queue items. Skips duplicates already pending or running."""
        added: list[QueueItem] = []
        rejected: list[dict] = []
        active = self._active_keys()

        for raw in pr_urls:
            url = raw.strip()
            if not url:
                continue
            try:
                repo_slug, pr_id = parse_pr_url(url, self.config.bitbucket_workspace)
            except ValueError as e:
                rejected.append({"input": url, "error": str(e)})
                continue
            key = self._key(repo_slug, pr_id)
            if key in active:
                rejected.append({"input": url, "error": "Already queued or in progress."})
                continue
            active.add(key)
            item = QueueItem(
                id=str(uuid.uuid4()),
                pr_url=url.strip().rstrip("/"),
                repo_slug=repo_slug,
                pr_id=pr_id,
                status="pending",
                enqueued_at=enqueued_at,
            )
            self._items.append(item)
            added.append(item)

        if added:
            self._wake.set()
        return added, rejected

    def list_items(self) -> list[dict]:
        return [item.to_dict() for item in self._items]

    def prune_finished(self, keep: int = 50) -> None:
        finished = [i for i in self._items if i.status in ("completed", "failed")]
        if len(finished) <= keep:
            return
        to_drop = len(finished) - keep
        drop_ids = {i.id for i in finished[:to_drop]}
        self._items = [i for i in self._items if i.id not in drop_ids]

    async def start_worker(self) -> None:
        if self._worker_task is not None:
            return
        self._worker_task = asyncio.create_task(self._run_worker())

    async def stop_worker(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    async def _run_worker(self) -> None:
        while True:
            item = await self._next_pending()
            if item is None:
                await self._wake.wait()
                self._wake.clear()
                continue
            await self._process_item(item)

    async def _next_pending(self) -> QueueItem | None:
        async with self._lock:
            for item in self._items:
                if item.status == "pending":
                    item.status = "running"
                    return item
        return None

    async def _process_item(self, item: QueueItem) -> None:
        from datetime import datetime, timezone

        try:
            result = await execute_review(
                self.config,
                self.client,
                item.repo_slug,
                item.pr_id,
            )
            item.outcome = result["outcome"]
            item.status = "completed"
        except Exception as e:
            item.status = "failed"
            item.error = str(e)
        finally:
            item.finished_at = datetime.now(timezone.utc).isoformat()
            async with self._lock:
                self.prune_finished()
            self._wake.set()
