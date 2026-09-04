import importlib
from unittest.mock import AsyncMock, patch

import pytest
from mcp.server.fastmcp import FastMCP

from src.bitbucket_client import BitbucketClient
from src.config import Config


def load_tool_functions(module_name: str) -> dict:
    """Return {tool_name: async_fn} after running register() on a tools module."""
    module = importlib.import_module(module_name)
    mcp = FastMCP("test")
    module.register(mcp)
    return {name: tool.fn for name, tool in mcp._tool_manager._tools.items()}


@pytest.fixture
def mock_config():
    return Config(
        bitbucket_email="test@example.com",
        bitbucket_api_token="test-token",
        bitbucket_workspace="test-workspace",
        project_keys=["PROJ1"],
        poll_interval_minutes=10,
        mcp_url="http://mcp:7390/mcp",
        mcp_config_path="/tmp/mcp-config.json",
        db_path="/tmp/reviewed.db",
    )


@pytest.fixture
def mock_bitbucket_client(mock_config):
    with patch("httpx.AsyncClient"):
        client = BitbucketClient(mock_config)
        client._http = AsyncMock()
        return client
