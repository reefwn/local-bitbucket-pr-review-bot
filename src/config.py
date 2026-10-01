import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _parse_project_keys() -> list[str]:
    return _parse_csv_env("PROJECT_KEYS")


def _parse_excluded_repo_slugs() -> list[str]:
    return _parse_csv_env("EXCLUDED_REPO_SLUGS")


@dataclass
class Config:
    bitbucket_email: str = os.getenv("BITBUCKET_EMAIL", "")
    bitbucket_api_token: str = os.getenv("BITBUCKET_API_TOKEN", "")
    bitbucket_workspace: str = os.getenv("BITBUCKET_WORKSPACE", "")
    project_keys: list[str] = field(default_factory=_parse_project_keys)
    excluded_repo_slugs: list[str] = field(default_factory=_parse_excluded_repo_slugs)
    poll_interval_minutes: int = int(os.getenv("POLL_INTERVAL_MINUTES", "10"))
    mcp_url: str = os.getenv("MCP_URL", "http://mcp:7390/mcp")
    mcp_config_path: str = os.getenv("MCP_CONFIG_PATH", "/app/mcp-config.json")
    kiro_mcp_config_path: str = os.getenv("KIRO_MCP_CONFIG_PATH", "/app/.kiro/agents/pr-reviewer.json")
    kiro_agent_name: str = os.getenv("KIRO_AGENT_NAME", "pr-reviewer")
    cursor_mcp_config_path: str = os.getenv("CURSOR_MCP_CONFIG_PATH", "/app/.cursor/mcp.json")
    cursor_workspace: str = os.getenv("CURSOR_WORKSPACE", "/app")
    cursor_model: str = os.getenv("CURSOR_MODEL", "")
    db_path: str = os.getenv("DB_PATH", "/data/reviewed_prs.db")

    @property
    def bitbucket_base_url(self) -> str:
        return "https://api.bitbucket.org/2.0"
