from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.bitbucket_client import BitbucketApiError, BitbucketClient


def _make_response(json_data=None, text="", status_code=200):
    r = MagicMock()
    r.is_error = status_code >= 400
    r.status_code = status_code
    r.json.return_value = json_data or {}
    r.text = text
    r.request = MagicMock(method="GET", url="https://api.bitbucket.org/2.0/test")
    return r


@pytest.mark.asyncio
async def test_bitbucket_client_init(mock_config):
    with patch("httpx.AsyncClient") as mock_client:
        client = BitbucketClient(mock_config)
        assert client.config == mock_config
        assert mock_client.call_count == 1


@pytest.mark.asyncio
async def test_get_success(mock_bitbucket_client):
    mock_bitbucket_client._http.get = AsyncMock(return_value=_make_response({"values": []}))
    result = await mock_bitbucket_client.get("/repositories/ws")
    assert result == {"values": []}


@pytest.mark.asyncio
async def test_get_retries_on_timeout_then_succeeds(mock_bitbucket_client):
    mock_bitbucket_client._http.get = AsyncMock(
        side_effect=[httpx.ReadTimeout("timed out"), _make_response({"values": []})]
    )
    with patch("asyncio.sleep", new=AsyncMock()):
        result = await mock_bitbucket_client.get("/repositories/ws")
    assert result == {"values": []}
    assert mock_bitbucket_client._http.get.call_count == 2


@pytest.mark.asyncio
async def test_get_raises_after_exhausting_retries(mock_bitbucket_client):
    mock_bitbucket_client._http.get = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
    with patch("asyncio.sleep", new=AsyncMock()):
        with pytest.raises(httpx.ReadTimeout):
            await mock_bitbucket_client.get("/repositories/ws")
    assert mock_bitbucket_client._http.get.call_count == 3


@pytest.mark.asyncio
async def test_get_all_pages_follows_next_link(mock_bitbucket_client):
    page1 = _make_response({"values": [{"slug": "repo-a"}], "next": "https://api.bitbucket.org/2.0/repositories/ws?page=2"})
    page2 = _make_response({"values": [{"slug": "repo-b"}]})
    mock_bitbucket_client._http.get = AsyncMock(side_effect=[page1, page2])
    result = await mock_bitbucket_client.get_all_pages("/repositories/ws", params={"pagelen": 100})
    assert result == [{"slug": "repo-a"}, {"slug": "repo-b"}]
    assert mock_bitbucket_client._http.get.call_count == 2
    first_call_url = mock_bitbucket_client._http.get.call_args_list[0].args[0]
    second_call_url = mock_bitbucket_client._http.get.call_args_list[1].args[0]
    assert first_call_url == "https://api.bitbucket.org/2.0/repositories/ws"
    assert second_call_url == "https://api.bitbucket.org/2.0/repositories/ws?page=2"


@pytest.mark.asyncio
async def test_get_all_pages_single_page(mock_bitbucket_client):
    mock_bitbucket_client._http.get = AsyncMock(return_value=_make_response({"values": [{"slug": "repo-a"}]}))
    result = await mock_bitbucket_client.get_all_pages("/repositories/ws")
    assert result == [{"slug": "repo-a"}]
    assert mock_bitbucket_client._http.get.call_count == 1


@pytest.mark.asyncio
async def test_get_text_success(mock_bitbucket_client):
    mock_bitbucket_client._http.get = AsyncMock(return_value=_make_response(text="diff content"))
    result = await mock_bitbucket_client.get_text("/diff")
    assert result == "diff content"


@pytest.mark.asyncio
async def test_post_success(mock_bitbucket_client):
    mock_bitbucket_client._http.post = AsyncMock(return_value=_make_response({"id": 1}))
    result = await mock_bitbucket_client.post("/comments", json={"content": {"raw": "hi"}})
    assert result == {"id": 1}


@pytest.mark.asyncio
async def test_api_error_includes_body(mock_bitbucket_client):
    resp = _make_response(status_code=400, text='{"error": {"message": "bad request"}}')
    mock_bitbucket_client._http.post = AsyncMock(return_value=resp)
    with pytest.raises(BitbucketApiError, match="bad request"):
        await mock_bitbucket_client.post("/comments", json={})


@pytest.mark.asyncio
async def test_close(mock_bitbucket_client):
    await mock_bitbucket_client.close()
    mock_bitbucket_client._http.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_get_own_account_uuid(mock_bitbucket_client):
    mock_bitbucket_client._http.get = AsyncMock(return_value=_make_response({"uuid": "{bot-uuid}"}))
    uuid = await mock_bitbucket_client.get_own_account_uuid()
    assert uuid == "{bot-uuid}"


@pytest.mark.asyncio
async def test_get_review_outcome_approved(mock_bitbucket_client):
    user_resp = _make_response({"uuid": "{bot-uuid}"})
    pr_resp = _make_response({
        "participants": [
            {"user": {"uuid": "{other-uuid}"}, "state": "changes_requested"},
            {"user": {"uuid": "{bot-uuid}"}, "state": "approved"},
        ]
    })
    mock_bitbucket_client._http.get = AsyncMock(side_effect=[user_resp, pr_resp])
    outcome = await mock_bitbucket_client.get_review_outcome("my-repo", 5)
    assert outcome == "approved"


@pytest.mark.asyncio
async def test_get_review_outcome_changes_requested(mock_bitbucket_client):
    user_resp = _make_response({"uuid": "{bot-uuid}"})
    pr_resp = _make_response({
        "participants": [
            {"user": {"uuid": "{bot-uuid}"}, "state": "changes_requested"},
        ]
    })
    mock_bitbucket_client._http.get = AsyncMock(side_effect=[user_resp, pr_resp])
    outcome = await mock_bitbucket_client.get_review_outcome("my-repo", 5)
    assert outcome == "changes_requested"


@pytest.mark.asyncio
async def test_get_review_outcome_unknown_when_no_matching_participant(mock_bitbucket_client):
    user_resp = _make_response({"uuid": "{bot-uuid}"})
    pr_resp = _make_response({
        "participants": [
            {"user": {"uuid": "{other-uuid}"}, "state": "approved"},
        ]
    })
    mock_bitbucket_client._http.get = AsyncMock(side_effect=[user_resp, pr_resp])
    outcome = await mock_bitbucket_client.get_review_outcome("my-repo", 5)
    assert outcome == "unknown"


@pytest.mark.asyncio
async def test_get_review_outcome_unknown_when_state_is_null(mock_bitbucket_client):
    user_resp = _make_response({"uuid": "{bot-uuid}"})
    pr_resp = _make_response({
        "participants": [
            {"user": {"uuid": "{bot-uuid}"}, "state": None, "role": "REVIEWER"},
        ]
    })
    mock_bitbucket_client._http.get = AsyncMock(side_effect=[user_resp, pr_resp])
    outcome = await mock_bitbucket_client.get_review_outcome("my-repo", 5)
    assert outcome == "unknown"
