from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.config import Config
from src.web.app import create_app


def _config(tmp_path):
    return Config(
        bitbucket_email="me@example.com",
        bitbucket_api_token="tok",
        bitbucket_workspace="my-ws",
        project_keys=[],
        poll_interval_minutes=10,
        mcp_url="http://mcp:7390/mcp",
        mcp_config_path=str(tmp_path / "mcp-config.json"),
        db_path=str(tmp_path / "reviewed.db"),
    )


def test_health(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.json() == {"status": "ok"}


def test_index_serves_form_with_url_input_and_submit(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "pr_url" in resp.text
    assert "<button" in resp.text


def test_review_rejects_malformed_url(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.post("/review", json={"pr_url": "not-a-url"})
    assert resp.status_code == 400


def test_review_success_marks_reviewed(tmp_path):
    config = _config(tmp_path)
    fake_client = AsyncMock()

    async def fake_get(path, params=None):
        if path.endswith("/comments"):
            return {"values": []}
        return {"source": {"commit": {"hash": "hash-a"}}}

    fake_client.get.side_effect = fake_get
    app = create_app(config, client=fake_client)

    with patch("src.web.app.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        client = TestClient(app)
        resp = client.post(
            "/review",
            json={"pr_url": "https://bitbucket.org/my-ws/my-repo/pull-requests/5"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["repo_slug"] == "my-repo"
    assert body["pr_id"] == 5
    mock_run_review.assert_called_once()

    from src.db import is_reviewed
    assert is_reviewed(config.db_path, "my-repo", 5, "hash-a") is True


def test_review_returns_json_error_when_run_review_fails(tmp_path):
    config = _config(tmp_path)
    fake_client = AsyncMock()

    async def fake_get(path, params=None):
        if path.endswith("/comments"):
            return {"values": []}
        return {"source": {"commit": {"hash": "hash-a"}}}

    fake_client.get.side_effect = fake_get
    app = create_app(config, client=fake_client)

    with patch("src.web.app.run_review") as mock_run_review:
        mock_run_review.side_effect = Exception("claude blew up")
        client = TestClient(app)
        resp = client.post(
            "/review",
            json={"pr_url": "https://bitbucket.org/my-ws/my-repo/pull-requests/5"},
        )

    assert resp.status_code == 502
    body = resp.json()
    assert "claude blew up" in body["detail"]
