from src.config import Config


def test_config_explicit_values():
    config = Config(
        bitbucket_email="me@example.com",
        bitbucket_api_token="tok",
        bitbucket_workspace="my-ws",
        project_keys=["PROJ1", "PROJ2"],
        poll_interval_minutes=5,
        mcp_url="http://mcp:7390/mcp",
        mcp_config_path="/tmp/mcp-config.json",
        db_path="/tmp/reviewed.db",
    )
    assert config.bitbucket_email == "me@example.com"
    assert config.project_keys == ["PROJ1", "PROJ2"]
    assert config.poll_interval_minutes == 5
    assert config.bitbucket_base_url == "https://api.bitbucket.org/2.0"


def test_config_defaults():
    config = Config()
    assert config.poll_interval_minutes == 10
    assert config.mcp_url == "http://mcp:7390/mcp"
    assert config.mcp_config_path == "/app/mcp-config.json"
    assert config.db_path == "/data/reviewed_prs.db"


def test_config_project_keys_parsed_from_env(monkeypatch):
    monkeypatch.setenv("PROJECT_KEYS", " PROJ1, PROJ2 ,,PROJ3")
    config = Config()
    assert config.project_keys == ["PROJ1", "PROJ2", "PROJ3"]
