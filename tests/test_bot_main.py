from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.main import resolve_repo_slugs, run_cycle
from src.config import Config
from src.db import init_db, is_reviewed

SOURCE_HASH_A = {"source": {"commit": {"hash": "hash-a"}}}
SOURCE_HASH_B = {"source": {"commit": {"hash": "hash-b"}}}


def _config(tmp_path):
    return Config(
        bitbucket_email="me@example.com",
        bitbucket_api_token="tok",
        bitbucket_workspace="my-ws",
        project_keys=["PROJ1"],
        poll_interval_minutes=10,
        mcp_url="http://mcp:7390/mcp",
        mcp_config_path=str(tmp_path / "mcp-config.json"),
        db_path=str(tmp_path / "reviewed.db"),
    )


@pytest.mark.asyncio
async def test_resolve_repo_slugs():
    client = AsyncMock()
    client.get.return_value = {"values": [{"slug": "repo-a"}, {"slug": "repo-b"}]}
    slugs = await resolve_repo_slugs(client, "my-ws", ["PROJ1"])
    assert slugs == ["repo-a", "repo-b"]
    params = client.get.call_args[1]["params"]
    assert params["q"] == 'project.key="PROJ1"'


@pytest.mark.asyncio
async def test_run_cycle_reviews_new_open_pr_and_marks_reviewed(tmp_path):
    config = _config(tmp_path)
    init_db(config.db_path)
    now = datetime.now(timezone.utc)
    recent_created = now.isoformat().replace("+00:00", "Z")

    client = AsyncMock()
    client.get_review_outcome.return_value = "approved"

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {"values": [{"id": 1, "state": "OPEN", "created_on": recent_created, **SOURCE_HASH_A}]}
        if path.endswith("/comments"):
            return {"values": []}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        await run_cycle(config, client)

    mock_run_review.assert_called_once()
    assert is_reviewed(config.db_path, "repo-a", 1, "hash-a") is True

    from src.db import list_recent_reviews

    reviews = list_recent_reviews(config.db_path)
    assert reviews[0]["outcome"] == "approved"


@pytest.mark.asyncio
async def test_run_cycle_records_unknown_outcome_when_lookup_fails(tmp_path):
    config = _config(tmp_path)
    init_db(config.db_path)
    now = datetime.now(timezone.utc)
    recent_created = now.isoformat().replace("+00:00", "Z")

    client = AsyncMock()
    client.get_review_outcome.side_effect = Exception("bitbucket api down")

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {"values": [{"id": 1, "state": "OPEN", "created_on": recent_created, **SOURCE_HASH_A}]}
        if path.endswith("/comments"):
            return {"values": []}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        await run_cycle(config, client)

    # The review itself still succeeds and gets recorded, just with an unknown outcome.
    assert is_reviewed(config.db_path, "repo-a", 1, "hash-a") is True

    from src.db import list_recent_reviews

    reviews = list_recent_reviews(config.db_path)
    assert reviews[0]["outcome"] == "unknown"


@pytest.mark.asyncio
async def test_run_cycle_skips_already_reviewed_pr(tmp_path):
    config = _config(tmp_path)
    init_db(config.db_path)
    from src.db import mark_reviewed

    now = datetime.now(timezone.utc)
    recent_created = now.isoformat().replace("+00:00", "Z")
    mark_reviewed(config.db_path, "repo-a", 1, now.isoformat(), "hash-a")

    client = AsyncMock()

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {"values": [{"id": 1, "state": "OPEN", "created_on": recent_created, **SOURCE_HASH_A}]}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        await run_cycle(config, client)

    mock_run_review.assert_not_called()


@pytest.mark.asyncio
async def test_run_cycle_reviews_pr_again_after_new_commit_pushed(tmp_path):
    config = _config(tmp_path)
    init_db(config.db_path)
    from src.db import mark_reviewed

    now = datetime.now(timezone.utc)
    recent_created = (now.replace(microsecond=0)).isoformat().replace("+00:00", "Z")
    mark_reviewed(config.db_path, "repo-a", 1, now.isoformat(), "hash-a")

    client = AsyncMock()
    client.get_review_outcome.return_value = "approved"

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {
                "values": [
                    {
                        "id": 1,
                        "state": "OPEN",
                        "created_on": recent_created,
                        "updated_on": recent_created,
                        **SOURCE_HASH_B,
                    }
                ]
            }
        if path.endswith("/comments"):
            return {"values": []}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        await run_cycle(config, client)

    mock_run_review.assert_called_once()
    assert is_reviewed(config.db_path, "repo-a", 1, "hash-b") is True


@pytest.mark.asyncio
async def test_run_cycle_continues_after_one_pr_fails(tmp_path):
    config = _config(tmp_path)
    init_db(config.db_path)
    now = datetime.now(timezone.utc)
    recent_created = now.isoformat().replace("+00:00", "Z")

    client = AsyncMock()
    client.get_review_outcome.return_value = "approved"

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {
                "values": [
                    {"id": 1, "state": "OPEN", "created_on": recent_created, **SOURCE_HASH_A},
                    {"id": 2, "state": "OPEN", "created_on": recent_created, **SOURCE_HASH_B},
                ]
            }
        if path.endswith("/comments"):
            return {"values": []}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        mock_run_review.side_effect = [Exception("boom"), {"result": "ok"}]
        await run_cycle(config, client)

    assert mock_run_review.call_count == 2
    assert is_reviewed(config.db_path, "repo-a", 1, "hash-a") is False
    assert is_reviewed(config.db_path, "repo-a", 2, "hash-b") is True
