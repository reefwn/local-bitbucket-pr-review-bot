import time
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.config import Config
from src.web.app import create_app

HOOK_SECRET = "test-hook-secret-shh"


def _config(tmp_path, *, review_hook_secret: str = "") -> Config:
    return Config(
        bitbucket_email="me@example.com",
        bitbucket_api_token="tok",
        bitbucket_workspace="my-ws",
        project_keys=[],
        poll_interval_minutes=10,
        mcp_url="http://mcp:7390/mcp",
        mcp_config_path=str(tmp_path / "mcp-config.json"),
        kiro_mcp_config_path=str(tmp_path / "pr-reviewer.json"),
        cursor_mcp_config_path=str(tmp_path / "workspace" / ".cursor" / "mcp.json"),
        cursor_workspace=str(tmp_path / "workspace"),
        db_path=str(tmp_path / "reviewed.db"),
        review_hook_secret=review_hook_secret,
    )


def test_hook_endpoint_unconfigured_returns_503(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.post(
        "/hooks/reviews/queue",
        json={"pr_urls": ["https://bitbucket.org/my-ws/repo/pull-requests/1"]},
        headers={"X-Review-Hook-Secret": "any"},
    )
    assert resp.status_code == 503


def test_hook_rejects_missing_or_wrong_secret(tmp_path):
    app = create_app(_config(tmp_path, review_hook_secret=HOOK_SECRET), client=AsyncMock())
    client = TestClient(app)
    url = "https://bitbucket.org/my-ws/repo/pull-requests/1"

    no_header = client.post("/hooks/reviews/queue", json={"pr_urls": [url]})
    assert no_header.status_code == 401

    bad = client.post(
        "/hooks/reviews/queue",
        json={"pr_urls": [url]},
        headers={"X-Review-Hook-Secret": "wrong"},
    )
    assert bad.status_code == 401


def test_hook_accepts_bearer_auth(tmp_path):
    app = create_app(_config(tmp_path, review_hook_secret=HOOK_SECRET), client=AsyncMock())
    client = TestClient(app)
    url = "https://bitbucket.org/my-ws/repo/pull-requests/1"
    resp = client.post(
        "/hooks/reviews/queue",
        json={"pr_urls": [url]},
        headers={"Authorization": f"Bearer {HOOK_SECRET}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()["enqueued"]) == 1


def test_reviews_queue_requires_secret_when_configured(tmp_path):
    app = create_app(_config(tmp_path, review_hook_secret=HOOK_SECRET), client=AsyncMock())
    client = TestClient(app)
    url = "https://bitbucket.org/my-ws/repo/pull-requests/1"

    blocked = client.post("/reviews/queue", json={"pr_urls": [url]})
    assert blocked.status_code == 401

    ok = client.post(
        "/reviews/queue",
        json={"pr_urls": [url]},
        headers={"X-Review-Hook-Secret": HOOK_SECRET},
    )
    assert ok.status_code == 200


def test_reviews_queue_unchanged_when_secret_unset(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    url = "https://bitbucket.org/my-ws/repo/pull-requests/1"
    resp = client.post("/reviews/queue", json={"pr_urls": [url]})
    assert resp.status_code == 200


def test_hook_enqueues_review(tmp_path):
    config = _config(tmp_path, review_hook_secret=HOOK_SECRET)
    fake_client = AsyncMock()
    fake_client.get_review_outcome.return_value = "approved"

    async def fake_get(path, *args, **kwargs):
        if path.endswith("/comments"):
            return {"values": []}
        return {"source": {"commit": {"hash": "abc123"}}}

    fake_client.get.side_effect = fake_get
    app = create_app(config, client=fake_client)
    url = "https://bitbucket.org/my-ws/repo/pull-requests/1"

    with patch("src.web.review_service.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        with TestClient(app) as client:
            resp = client.post(
                "/hooks/reviews/queue",
                json={"pr_urls": [url]},
                headers={"X-Review-Hook-Secret": HOOK_SECRET},
            )
            assert resp.status_code == 200

            for _ in range(100):
                queue = client.get("/reviews/queue").json()["items"]
                if queue and all(i["status"] in ("completed", "failed") for i in queue):
                    break
                time.sleep(0.05)

            assert mock_run_review.call_count == 1