from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from src.config import Config
from src.web.app import create_app

ADMIN_PW = "admin-secret"
USER_PW = "user-secret"


def _config(tmp_path, **overrides) -> Config:
    defaults = dict(
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
        review_ui_admin_password=ADMIN_PW,
        review_ui_user_password=USER_PW,
        review_ui_session_key="test-session-key",
    )
    defaults.update(overrides)
    return Config(**defaults)


def _login(client: TestClient, password: str) -> None:
    resp = client.post("/auth/login", json={"password": password})
    assert resp.status_code == 200


def test_auth_disabled_when_passwords_unset(tmp_path):
    app = create_app(_config(tmp_path, review_ui_admin_password="", review_ui_user_password=""), client=AsyncMock())
    client = TestClient(app)
    assert client.get("/auth/me").json() == {
        "auth_required": False,
        "misconfigured": False,
        "role": None,
    }
    assert client.get("/reviews").status_code == 200


def test_auth_misconfigured_when_only_one_password_set(tmp_path):
    app = create_app(_config(tmp_path, review_ui_user_password=""), client=AsyncMock())
    client = TestClient(app)
    body = client.get("/auth/me").json()
    assert body["auth_required"] is False
    assert body["misconfigured"] is True

    resp = client.post("/auth/login", json={"password": ADMIN_PW})
    assert resp.status_code == 400


def test_user_can_enqueue_but_not_change_providers(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)

    assert client.get("/reviews").status_code == 401
    assert client.get("/config/providers").status_code == 401

    _login(client, USER_PW)
    assert client.get("/auth/me").json()["role"] == "user"

    url = "https://bitbucket.org/my-ws/my-repo/pull-requests/1"
    queued = client.post("/reviews/queue", json={"pr_urls": [url]})
    assert queued.status_code == 200

    denied = client.put("/config/providers", json={"providers": ["codex", "kiro", "cursor", "claude"]})
    assert denied.status_code == 403


def test_admin_can_configure_providers(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    _login(client, ADMIN_PW)

    assert client.get("/config/providers").status_code == 200
    updated = client.put("/config/providers", json={"providers": ["codex", "kiro", "cursor", "claude"]})
    assert updated.status_code == 200


def test_invalid_password_rejected(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    resp = client.post("/auth/login", json={"password": "nope"})
    assert resp.status_code == 401


def test_logout_clears_session(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    _login(client, ADMIN_PW)
    assert client.post("/auth/logout").status_code == 200
    assert client.get("/reviews").status_code == 401


def test_login_gate_markup_in_index(tmp_path):
    app = create_app(_config(tmp_path), client=AsyncMock())
    client = TestClient(app)
    html = client.get("/").text
    assert "login-gate" in html
    assert "login-password" in html