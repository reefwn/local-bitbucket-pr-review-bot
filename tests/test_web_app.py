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
        kiro_mcp_config_path=str(tmp_path / "kiro-mcp-config.json"),
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


def test_reviews_empty_before_any_review(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.get("/reviews")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviews"] == []
    assert body["total"] == 0
    assert body["page"] == 1
    assert body["total_pages"] == 1


def test_reviews_lists_reviewed_prs_after_a_successful_review(tmp_path):
    config = _config(tmp_path)
    fake_client = AsyncMock()
    fake_client.get_review_outcome.return_value = "approved"

    async def fake_get(path, params=None):
        if path.endswith("/comments"):
            return {"values": []}
        return {"source": {"commit": {"hash": "hash-a"}}}

    fake_client.get.side_effect = fake_get
    app = create_app(config, client=fake_client)

    with patch("src.web.app.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        client = TestClient(app)
        client.post("/review", json={"pr_url": "https://bitbucket.org/my-ws/my-repo/pull-requests/5"})
        resp = client.get("/reviews")

    assert resp.status_code == 200
    body = resp.json()
    reviews = body["reviews"]
    assert len(reviews) == 1
    assert reviews[0]["repo_slug"] == "my-repo"
    assert reviews[0]["pr_id"] == 5
    assert reviews[0]["pr_url"] == "https://bitbucket.org/my-ws/my-repo/pull-requests/5"
    assert reviews[0]["outcome"] == "approved"
    assert body["total"] == 1


def test_reviews_paginates_at_ten_per_page_by_default(tmp_path):
    config = _config(tmp_path)
    from src.db import init_db, mark_reviewed

    init_db(config.db_path)
    for i in range(25):
        mark_reviewed(config.db_path, "my-repo", i, f"2026-09-04T{i:02d}:00:00+00:00", f"hash-{i}")

    app = create_app(config, client=AsyncMock())
    client = TestClient(app)

    page_1 = client.get("/reviews").json()
    assert len(page_1["reviews"]) == 10
    assert page_1["page"] == 1
    assert page_1["total"] == 25
    assert page_1["total_pages"] == 3
    assert page_1["reviews"][0]["pr_id"] == 24

    page_3 = client.get("/reviews?page=3").json()
    assert len(page_3["reviews"]) == 5
    assert page_3["page"] == 3

    page_4 = client.get("/reviews?page=4").json()
    assert page_4["reviews"] == []


def test_review_rejects_malformed_url(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.post("/review", json={"pr_url": "not-a-url"})
    assert resp.status_code == 400


def test_review_success_marks_reviewed(tmp_path):
    config = _config(tmp_path)
    fake_client = AsyncMock()
    fake_client.get_review_outcome.return_value = "approved"

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
    assert body["outcome"] == "approved"
    mock_run_review.assert_called_once()

    from src.db import is_reviewed
    assert is_reviewed(config.db_path, "my-repo", 5, "hash-a") is True


def test_review_records_changes_requested_outcome(tmp_path):
    config = _config(tmp_path)
    fake_client = AsyncMock()
    fake_client.get_review_outcome.return_value = "changes_requested"

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
    assert resp.json()["outcome"] == "changes_requested"

    from src.db import list_recent_reviews
    reviews = list_recent_reviews(config.db_path)
    assert reviews[0]["outcome"] == "changes_requested"


def test_review_records_unknown_outcome_when_lookup_fails(tmp_path):
    config = _config(tmp_path)
    fake_client = AsyncMock()
    fake_client.get_review_outcome.side_effect = Exception("bitbucket api down")

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

    # The review itself still succeeds; outcome just falls back to 'unknown'.
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "unknown"


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
