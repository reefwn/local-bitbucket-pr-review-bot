import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _parse_project_keys() -> list[str]:
    raw = os.getenv("PROJECT_KEYS", "")
    return [key.strip() for key in raw.split(",") if key.strip()]


@dataclass
class Config:
    bitbucket_email: str = os.getenv("BITBUCKET_EMAIL", "")
    bitbucket_api_token: str = os.getenv("BITBUCKET_API_TOKEN", "")
    bitbucket_workspace: str = os.getenv("BITBUCKET_WORKSPACE", "")
    project_keys: list[str] = field(default_factory=_parse_project_keys)
    poll_interval_minutes: int = int(os.getenv("POLL_INTERVAL_MINUTES", "10"))
    mcp_url: str = os.getenv("MCP_URL", "http://mcp:7390/mcp")
    mcp_config_path: str = os.getenv("MCP_CONFIG_PATH", "/app/mcp-config.json")
    db_path: str = os.getenv("DB_PATH", "/data/reviewed_prs.db")

    @property
    def bitbucket_base_url(self) -> str:
        return "https://api.bitbucket.org/2.0"
