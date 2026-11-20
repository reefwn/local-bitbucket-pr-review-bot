from unittest.mock import MagicMock, patch

from src.web.provider_auth import (
    ProviderLoginStatus,
    check_claude_login,
    check_codex_login,
    check_cursor_login,
    check_kiro_login,
    collect_provider_login_status,
)


def test_check_claude_logged_in():
    with patch("src.web.provider_auth._run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"loggedIn": true, "authMethod": "claude.ai"}',
            stderr="",
        )
        status = check_claude_login()
    assert status.logged_in is True
    assert status.summary == "claude.ai"


def test_check_claude_logged_out():
    with patch("src.web.provider_auth._run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout='{"loggedIn": false}',
            stderr="",
        )
        status = check_claude_login()
    assert status.logged_in is False


def test_check_codex_logged_in():
    with patch("src.web.provider_auth._run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="✓ Logged in as me@example.com\n",
            stderr="",
        )
        status = check_codex_login()
    assert status.logged_in is True
    assert "me@example.com" in status.summary


def test_check_cursor_logged_out():
    with patch("src.web.provider_auth._run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="Not logged in",
        )
        status = check_cursor_login()
    assert status.logged_in is False


def test_check_kiro_logged_in():
    with patch("src.web.provider_auth._run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="user@example.com\n", stderr="")
        status = check_kiro_login()
    assert status.logged_in is True


def test_collect_provider_login_status():
    logged = ProviderLoginStatus("claude", True, "ok")
    with (
        patch("src.web.provider_auth.check_claude_login", return_value=logged),
        patch("src.web.provider_auth.check_codex_login", return_value=logged),
        patch("src.web.provider_auth.check_cursor_login", return_value=logged),
        patch("src.web.provider_auth.check_kiro_login", return_value=logged),
    ):
        results = collect_provider_login_status()
    assert len(results) == 4
    assert all(row["logged_in"] for row in results)