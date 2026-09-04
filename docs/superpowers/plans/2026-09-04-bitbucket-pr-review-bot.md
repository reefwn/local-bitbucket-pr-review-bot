# Bitbucket PR Review Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Bitbucket PR review bot: a poller that finds newly-opened PRs every N minutes and reviews them with headless Claude, a bundled PR-only Bitbucket MCP server, and a localhost web page to trigger an immediate review by pasting a PR URL.

**Architecture:** Three Docker Compose services built from one shared image (`mcp`, `bot`, `web`), differing only in their `command:`. `mcp` runs a FastMCP streamable-HTTP server exposing PR-only Bitbucket tools. `bot` runs an asyncio poll loop. `web` runs a FastAPI app. `bot` and `web` both call the same `review_runner` module, which shells out to headless `claude -p` configured to reach `mcp` over HTTP, and both share a SQLite file (`reviewed_prs`) and a Claude CLI auth volume.

**Tech Stack:** Python 3.11+, `mcp[cli]` (FastMCP), `httpx`, `fastapi` + `uvicorn`, stdlib `sqlite3`, `pytest` + `pytest-asyncio`, Docker Compose, Claude Code CLI (Pro-subscription OAuth login, no API key).

**Spec:** `docs/superpowers/specs/2026-09-04-bitbucket-pr-review-bot-design.md`

## Global Constraints

- Only PRs with `state == "OPEN"` are ever fetched for review; merged/declined PRs are never touched.
- Only PRs are considered whose `created_on` falls within the last `POLL_INTERVAL_MINUTES` (bot poll path only — the web trigger always reviews regardless of age or prior review state).
- Existing PR comments are passed into the review prompt as context; the bot/web never reply to individual comment threads.
- The web trigger has no authentication (localhost-only use) and always re-reviews, updating the `reviewed_prs` row on success so the bot doesn't duplicate it later.
- Single Bitbucket workspace per deployment (`BITBUCKET_WORKSPACE`); no multi-workspace support.
- Bitbucket auth is HTTP Basic with `BITBUCKET_EMAIL` + `BITBUCKET_API_TOKEN` against `https://api.bitbucket.org/2.0`.
- Claude auth is via Claude Code CLI OAuth login (Pro subscription, not an API key) — a persisted `claude-auth` volume, populated once via `docker compose run --rm bot claude login`.
- `reviewed_prs` SQLite schema is exactly:
  ```sql
  CREATE TABLE reviewed_prs (
      repo_slug   TEXT NOT NULL,
      pr_id       INTEGER NOT NULL,
      reviewed_at TEXT NOT NULL,
      PRIMARY KEY (repo_slug, pr_id)
  );
  ```
- Headless review invocation is exactly:
  ```
  claude -p "<prompt>" \
    --mcp-config <mcp_config_path> \
    --allowedTools "mcp__bitbucket-pr__bitbucket_get_pr,mcp__bitbucket-pr__bitbucket_get_pr_diff,mcp__bitbucket-pr__bitbucket_list_pr_comments,mcp__bitbucket-pr__bitbucket_create_pr_comment" \
    --output-format json
  ```
  Claude fetches the diff/comments and posts the review comment itself via MCP — the runner never constructs or posts comment text.
- MCP server name in `mcp-config.json` is `bitbucket-pr`, exposing exactly these tools: `bitbucket_list_repos`, `bitbucket_get_repo`, `bitbucket_list_prs`, `bitbucket_get_pr`, `bitbucket_get_pr_diff`, `bitbucket_list_pr_comments`, `bitbucket_create_pr_comment`.

---

## Task 1: Project scaffolding + Config

**Files:**
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `src/config.py`
- Create: `tests/__init__.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `src.config.Config` dataclass with fields `bitbucket_email: str`, `bitbucket_api_token: str`, `bitbucket_workspace: str`, `project_keys: list[str]`, `poll_interval_minutes: int`, `mcp_url: str`, `mcp_config_path: str`, `db_path: str`, and property `bitbucket_base_url -> str`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "bitbucket-pr-review-bot"
version = "0.1.0"
description = "Poll Bitbucket for newly-opened PRs and review them with headless Claude"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0,<2.0.0",
    "httpx>=0.27.0",
    "python-dotenv>=1.0.0",
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
]

[project.optional-dependencies]
test = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
addopts = "-v --tb=short"
```

- [ ] **Step 2: Create package skeleton**

```bash
mkdir -p src tests
touch src/__init__.py tests/__init__.py
```

- [ ] **Step 3: Install the project in editable mode with test deps**

Run: `pip install -e ".[test]"`
Expected: installs successfully.

- [ ] **Step 4: Write the failing test for `Config`**

`tests/test_config.py`:
```python
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
```

- [ ] **Step 5: Run tests to verify they fail**

Run: `pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 6: Write `src/config.py`**

```python
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
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS (3 tests)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/__init__.py src/config.py tests/__init__.py tests/test_config.py
git commit -m "Add project scaffolding and Config"
```

---

## Task 2: Bitbucket PR URL parser

**Files:**
- Create: `src/pr_url.py`
- Create: `tests/test_pr_url.py`

**Interfaces:**
- Consumes: nothing (pure module).
- Produces: `src.pr_url.parse_pr_url(url: str, expected_workspace: str) -> tuple[str, int]`, raising `ValueError` on a malformed URL or workspace mismatch.

- [ ] **Step 1: Write the failing tests**

`tests/test_pr_url.py`:
```python
import pytest

from src.pr_url import parse_pr_url


def test_parse_pr_url_basic():
    repo_slug, pr_id = parse_pr_url(
        "https://bitbucket.org/my-ws/my-repo/pull-requests/42", "my-ws"
    )
    assert repo_slug == "my-repo"
    assert pr_id == 42


def test_parse_pr_url_trailing_slash():
    repo_slug, pr_id = parse_pr_url(
        "https://bitbucket.org/my-ws/my-repo/pull-requests/42/", "my-ws"
    )
    assert repo_slug == "my-repo"
    assert pr_id == 42


def test_parse_pr_url_strips_whitespace():
    repo_slug, pr_id = parse_pr_url(
        "  https://bitbucket.org/my-ws/my-repo/pull-requests/42  ", "my-ws"
    )
    assert repo_slug == "my-repo"
    assert pr_id == 42


def test_parse_pr_url_wrong_workspace():
    with pytest.raises(ValueError, match="does not match configured workspace"):
        parse_pr_url("https://bitbucket.org/other-ws/my-repo/pull-requests/42", "my-ws")


def test_parse_pr_url_malformed():
    with pytest.raises(ValueError, match="Invalid Bitbucket PR URL"):
        parse_pr_url("https://example.com/not-a-pr-url", "my-ws")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pr_url.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pr_url'`

- [ ] **Step 3: Write `src/pr_url.py`**

```python
import re

_PR_URL_RE = re.compile(
    r"^https://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo_slug>[^/]+)/pull-requests/(?P<pr_id>\d+)/?$"
)


def parse_pr_url(url: str, expected_workspace: str) -> tuple[str, int]:
    """Parse a Bitbucket PR URL into (repo_slug, pr_id).

    Raises ValueError if the URL doesn't match the expected Bitbucket PR URL
    shape, or if its workspace doesn't match expected_workspace.
    """
    match = _PR_URL_RE.match(url.strip())
    if not match:
        raise ValueError(
            f"Invalid Bitbucket PR URL: {url!r}. Expected "
            "'https://bitbucket.org/{workspace}/{repo_slug}/pull-requests/{pr_id}'."
        )
    workspace = match.group("workspace")
    if workspace != expected_workspace:
        raise ValueError(
            f"PR URL workspace {workspace!r} does not match configured workspace {expected_workspace!r}."
        )
    return match.group("repo_slug"), int(match.group("pr_id"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pr_url.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/pr_url.py tests/test_pr_url.py
git commit -m "Add Bitbucket PR URL parser"
```

---

## Task 3: SQLite reviewed_prs store

**Files:**
- Create: `src/db.py`
- Create: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `src.db.init_db(db_path: str) -> None`, `src.db.is_reviewed(db_path: str, repo_slug: str, pr_id: int) -> bool`, `src.db.mark_reviewed(db_path: str, repo_slug: str, pr_id: int, reviewed_at: str) -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py`:
```python
from src.db import init_db, is_reviewed, mark_reviewed


def test_is_reviewed_false_before_marking(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    assert is_reviewed(db_path, "my-repo", 1) is False


def test_mark_reviewed_then_is_reviewed_true(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00")
    assert is_reviewed(db_path, "my-repo", 1) is True


def test_mark_reviewed_is_idempotent(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00")
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T01:00:00+00:00")
    assert is_reviewed(db_path, "my-repo", 1) is True


def test_reviewed_prs_scoped_by_repo_and_id(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "repo-a", 1, "2026-09-04T00:00:00+00:00")
    assert is_reviewed(db_path, "repo-b", 1) is False
    assert is_reviewed(db_path, "repo-a", 2) is False


def test_init_db_creates_parent_directory(tmp_path):
    db_path = str(tmp_path / "nested" / "dir" / "reviewed.db")
    init_db(db_path)
    assert is_reviewed(db_path, "my-repo", 1) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.db'`

- [ ] **Step 3: Write `src/db.py`**

```python
import sqlite3
from pathlib import Path


def init_db(db_path: str) -> None:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reviewed_prs (
                repo_slug   TEXT NOT NULL,
                pr_id       INTEGER NOT NULL,
                reviewed_at TEXT NOT NULL,
                PRIMARY KEY (repo_slug, pr_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def is_reviewed(db_path: str, repo_slug: str, pr_id: int) -> bool:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM reviewed_prs WHERE repo_slug = ? AND pr_id = ?",
            (repo_slug, pr_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_reviewed(db_path: str, repo_slug: str, pr_id: int, reviewed_at: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT OR REPLACE INTO reviewed_prs (repo_slug, pr_id, reviewed_at) VALUES (?, ?, ?)",
            (repo_slug, pr_id, reviewed_at),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_db.py
git commit -m "Add SQLite reviewed_prs store"
```

---

## Task 4: Bitbucket client

**Files:**
- Create: `src/bitbucket_client.py`
- Create: `tests/conftest.py`
- Create: `tests/test_bitbucket_client.py`

**Interfaces:**
- Consumes: `src.config.Config` (Task 1).
- Produces: `src.bitbucket_client.BitbucketApiError(response: httpx.Response)`, `src.bitbucket_client.BitbucketClient(config: Config)` with async methods `get(path, params=None) -> dict`, `get_text(path, params=None) -> str`, `post(path, json) -> dict`, `close() -> None`.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
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
```

- [ ] **Step 2: Write the failing tests**

`tests/test_bitbucket_client.py`:
```python
from unittest.mock import AsyncMock, MagicMock, patch

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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_bitbucket_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bitbucket_client'`

- [ ] **Step 4: Write `src/bitbucket_client.py`**

```python
import httpx

from src.config import Config


class BitbucketApiError(Exception):
    """Raised when the Bitbucket API returns an error. Message includes status, URL, and response body."""

    def __init__(self, response: httpx.Response):
        body = response.text.strip()
        self.status_code = response.status_code
        self.method = response.request.method
        self.url = str(response.request.url)
        self.body = body
        super().__init__(
            f"Bitbucket API error {response.status_code} {self.method} {self.url}\n{body or '(empty response body)'}"
        )


class BitbucketClient:
    """Bitbucket Cloud API client (PR-related endpoints only)."""

    def __init__(self, config: Config):
        self.config = config
        self._http = httpx.AsyncClient(
            auth=(config.bitbucket_email, config.bitbucket_api_token),
            headers={"Accept": "application/json"},
            timeout=30.0,
            follow_redirects=True,
        )

    @staticmethod
    def _check_response(response: httpx.Response) -> None:
        if response.is_error:
            raise BitbucketApiError(response)

    async def get(self, path: str, params: dict | None = None) -> dict:
        r = await self._http.get(f"{self.config.bitbucket_base_url}{path}", params=params)
        self._check_response(r)
        return r.json()

    async def get_text(self, path: str, params: dict | None = None) -> str:
        r = await self._http.get(f"{self.config.bitbucket_base_url}{path}", params=params)
        self._check_response(r)
        return r.text

    async def post(self, path: str, json: dict) -> dict:
        r = await self._http.post(f"{self.config.bitbucket_base_url}{path}", json=json)
        self._check_response(r)
        return r.json()

    async def close(self) -> None:
        await self._http.aclose()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_bitbucket_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add src/bitbucket_client.py tests/conftest.py tests/test_bitbucket_client.py
git commit -m "Add Bitbucket client"
```

---

## Task 5: Review runner

**Files:**
- Create: `src/review_runner.py`
- Create: `tests/test_review_runner.py`

**Interfaces:**
- Consumes: nothing beyond stdlib `subprocess`/`json`.
- Produces: `src.review_runner.build_prompt(repo_slug: str, pr_id: int, existing_comments: str) -> str`, `src.review_runner.write_mcp_config(path: str, mcp_url: str) -> None`, `src.review_runner.run_review(repo_slug: str, pr_id: int, existing_comments: str, mcp_config_path: str) -> dict` (raises `subprocess.CalledProcessError` on failure).

- [ ] **Step 1: Write the failing tests**

`tests/test_review_runner.py`:
```python
import json
import subprocess
from unittest.mock import MagicMock, patch

from src.review_runner import build_prompt, run_review, write_mcp_config


def test_build_prompt_includes_repo_and_pr():
    prompt = build_prompt("my-repo", 42, "")
    assert "my-repo" in prompt
    assert "#42" in prompt


def test_build_prompt_includes_existing_comments():
    prompt = build_prompt("my-repo", 42, "[Alice] please add tests")
    assert "[Alice] please add tests" in prompt


def test_build_prompt_no_comments_placeholder():
    prompt = build_prompt("my-repo", 42, "")
    assert "(none)" in prompt


def test_write_mcp_config(tmp_path):
    path = str(tmp_path / "mcp-config.json")
    write_mcp_config(path, "http://mcp:7390/mcp")
    with open(path) as f:
        config = json.load(f)
    assert config == {
        "mcpServers": {
            "bitbucket-pr": {"type": "http", "url": "http://mcp:7390/mcp"}
        }
    }


def test_run_review_invokes_claude_with_expected_args(tmp_path):
    config_path = str(tmp_path / "mcp-config.json")
    fake_result = MagicMock(stdout=json.dumps({"result": "posted comment"}))
    with patch("subprocess.run", return_value=fake_result) as mock_run:
        result = run_review("my-repo", 42, "", config_path)
    assert result == {"result": "posted comment"}
    args = mock_run.call_args[0][0]
    assert args[0] == "claude"
    assert "-p" in args
    assert "--mcp-config" in args
    assert config_path in args
    assert "--allowedTools" in args
    assert "mcp__bitbucket-pr__bitbucket_create_pr_comment" in args[args.index("--allowedTools") + 1]
    assert "--output-format" in args
    assert "json" in args


def test_run_review_propagates_failure(tmp_path):
    config_path = str(tmp_path / "mcp-config.json")
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["claude"]),
    ):
        try:
            run_review("my-repo", 42, "", config_path)
            assert False, "expected CalledProcessError"
        except subprocess.CalledProcessError:
            pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_review_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.review_runner'`

- [ ] **Step 3: Write `src/review_runner.py`**

```python
import json
import subprocess

_ALLOWED_TOOLS = (
    "mcp__bitbucket-pr__bitbucket_get_pr,"
    "mcp__bitbucket-pr__bitbucket_get_pr_diff,"
    "mcp__bitbucket-pr__bitbucket_list_pr_comments,"
    "mcp__bitbucket-pr__bitbucket_create_pr_comment"
)


def build_prompt(repo_slug: str, pr_id: int, existing_comments: str) -> str:
    """Build the headless review prompt for a single PR."""
    comments_section = existing_comments.strip() or "(none)"
    return (
        f"Review pull request #{pr_id} in the '{repo_slug}' Bitbucket repository. "
        "Fetch its diff and existing comments using the available Bitbucket tools, "
        "then post a single review comment via bitbucket_create_pr_comment covering "
        "correctness, style, and test coverage.\n\n"
        f"Existing comments on this PR (do not repeat points already raised):\n{comments_section}"
    )


def write_mcp_config(path: str, mcp_url: str) -> None:
    """Write the MCP config file Claude Code needs to reach the bundled Bitbucket-PR MCP server."""
    config = {"mcpServers": {"bitbucket-pr": {"type": "http", "url": mcp_url}}}
    with open(path, "w") as f:
        json.dump(config, f)


def run_review(repo_slug: str, pr_id: int, existing_comments: str, mcp_config_path: str) -> dict:
    """Run headless Claude to review and comment on a PR. Returns the parsed JSON result.

    Raises subprocess.CalledProcessError if the claude invocation fails.
    """
    prompt = build_prompt(repo_slug, pr_id, existing_comments)
    result = subprocess.run(
        [
            "claude", "-p", prompt,
            "--mcp-config", mcp_config_path,
            "--allowedTools", _ALLOWED_TOOLS,
            "--output-format", "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_review_runner.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add src/review_runner.py tests/test_review_runner.py
git commit -m "Add headless review runner"
```

---

## Task 6: Bitbucket PR-only MCP server

**Files:**
- Create: `src/mcp_server/__init__.py`
- Create: `src/mcp_server/tools.py`
- Create: `src/mcp_server/server.py`
- Create: `tests/test_mcp_tools.py`

**Interfaces:**
- Consumes: `src.bitbucket_client.BitbucketClient`, `src.config.Config` (Task 1, 4).
- Produces: `src.mcp_server.tools.register(mcp: FastMCP) -> None` registering `bitbucket_list_repos`, `bitbucket_get_repo`, `bitbucket_list_prs`, `bitbucket_get_pr`, `bitbucket_get_pr_diff`, `bitbucket_list_pr_comments`, `bitbucket_create_pr_comment`; `src.mcp_server.server.main()` entrypoint.

- [ ] **Step 1: Write `src/mcp_server/__init__.py`**

```bash
touch src/mcp_server/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/test_mcp_tools.py`:
```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import load_tool_functions

_tools = load_tool_functions("src.mcp_server.tools")
bitbucket_list_repos = _tools["bitbucket_list_repos"]
bitbucket_get_repo = _tools["bitbucket_get_repo"]
bitbucket_list_prs = _tools["bitbucket_list_prs"]
bitbucket_get_pr = _tools["bitbucket_get_pr"]
bitbucket_get_pr_diff = _tools["bitbucket_get_pr_diff"]
bitbucket_list_pr_comments = _tools["bitbucket_list_pr_comments"]
bitbucket_create_pr_comment = _tools["bitbucket_create_pr_comment"]


def _patches():
    mock_client = AsyncMock()
    mock_config = MagicMock()
    mock_config.bitbucket_workspace = "test-ws"
    return mock_client, mock_config


@pytest.mark.asyncio
async def test_bitbucket_list_repos_with_results():
    mc, cfg = _patches()
    mc.get.return_value = {
        "values": [{"slug": "repo1", "description": "Desc", "updated_on": "2023-01-01T00:00:00Z"}]
    }
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_list_repos()
    assert "[repo1]" in result


@pytest.mark.asyncio
async def test_bitbucket_get_repo():
    mc, cfg = _patches()
    mc.get.return_value = {
        "slug": "repo1", "name": "Repo One", "description": "",
        "mainbranch": {"name": "main"},
    }
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_get_repo("repo1")
    assert result["mainbranch"] == "main"


@pytest.mark.asyncio
async def test_bitbucket_list_prs_basic():
    mc, cfg = _patches()
    mc.get.return_value = {
        "values": [{"id": 1, "title": "PR 1", "author": {"display_name": "John"}, "state": "OPEN"}]
    }
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_list_prs("repo")
    assert "[PR #1]" in result


@pytest.mark.asyncio
async def test_bitbucket_get_pr_success():
    mc, cfg = _patches()
    mc.get.return_value = {
        "id": 1, "title": "PR", "state": "OPEN",
        "author": {"display_name": "John"},
        "source": {"branch": {"name": "feat"}},
        "destination": {"branch": {"name": "main"}},
        "description": "desc",
    }
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_get_pr("repo", 1)
    assert result["author"] == "John"
    assert result["source"] == "feat"


@pytest.mark.asyncio
async def test_bitbucket_get_pr_diff():
    mc, cfg = _patches()
    mc.get_text.return_value = "diff --git a/f b/f"
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_get_pr_diff("repo", 1)
    assert result.startswith("diff")


@pytest.mark.asyncio
async def test_bitbucket_list_pr_comments():
    mc, cfg = _patches()
    mc.get.return_value = {
        "values": [
            {"user": {"display_name": "John"}, "content": {"raw": "LGTM"}},
            {"user": {"display_name": "Bob"}, "content": {}},
        ]
    }
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_list_pr_comments("repo", 1)
    assert "[John] LGTM" in result
    assert "Bob" not in result


@pytest.mark.asyncio
async def test_bitbucket_create_pr_comment():
    mc, cfg = _patches()
    mc.post.return_value = {"id": "c1"}
    with patch("src.mcp_server.tools.client", mc), patch("src.mcp_server.tools.config", cfg):
        result = await bitbucket_create_pr_comment("repo", 1, "Nice")
    assert result == {"id": "c1"}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.mcp_server.tools'`

- [ ] **Step 4: Write `src/mcp_server/tools.py`**

```python
from mcp.server.fastmcp import FastMCP

from src.bitbucket_client import BitbucketClient
from src.config import Config

config = Config()
client = BitbucketClient(config)


def register(mcp: FastMCP) -> None:
    @mcp.tool()
    async def bitbucket_list_repos(limit: int = 10) -> str:
        """List repositories in the configured Bitbucket workspace."""
        ws = config.bitbucket_workspace
        data = await client.get(f"/repositories/{ws}", params={"pagelen": limit})
        repos = data.get("values", [])
        return "\n".join(
            f"[{r['slug']}] {r.get('description', 'No description')} (Updated: {r['updated_on'][:10]})"
            for r in repos
        ) or "No repositories found."

    @mcp.tool()
    async def bitbucket_get_repo(repo_slug: str) -> dict:
        """Get repository details."""
        ws = config.bitbucket_workspace
        data = await client.get(f"/repositories/{ws}/{repo_slug}")
        return {
            "slug": data["slug"],
            "name": data["name"],
            "description": data.get("description", ""),
            "mainbranch": data.get("mainbranch", {}).get("name", ""),
        }

    @mcp.tool()
    async def bitbucket_list_prs(repo_slug: str, state: str = "OPEN") -> str:
        """List pull requests for a Bitbucket repository. State: OPEN, MERGED, DECLINED."""
        ws = config.bitbucket_workspace
        data = await client.get(
            f"/repositories/{ws}/{repo_slug}/pullrequests",
            params={"state": state},
        )
        prs = data.get("values", [])
        return "\n".join(
            f"[PR #{p['id']}] {p['title']} by {p['author']['display_name']} ({p['state']})"
            for p in prs
        ) or "No pull requests found."

    @mcp.tool()
    async def bitbucket_get_pr(repo_slug: str, pr_id: int) -> dict:
        """Get details of a specific Bitbucket pull request."""
        ws = config.bitbucket_workspace
        data = await client.get(f"/repositories/{ws}/{repo_slug}/pullrequests/{pr_id}")
        return {
            "id": data["id"],
            "title": data["title"],
            "state": data["state"],
            "author": data["author"]["display_name"],
            "source": data["source"]["branch"]["name"],
            "destination": data["destination"]["branch"]["name"],
            "description": data.get("description", ""),
        }

    @mcp.tool()
    async def bitbucket_get_pr_diff(repo_slug: str, pr_id: int) -> str:
        """Get the full diff of a pull request."""
        ws = config.bitbucket_workspace
        return await client.get_text(f"/repositories/{ws}/{repo_slug}/pullrequests/{pr_id}/diff")

    @mcp.tool()
    async def bitbucket_list_pr_comments(repo_slug: str, pr_id: int) -> str:
        """List comments on a pull request."""
        ws = config.bitbucket_workspace
        data = await client.get(f"/repositories/{ws}/{repo_slug}/pullrequests/{pr_id}/comments")
        comments = data.get("values", [])
        return "\n".join(
            f"[{c['user']['display_name']}] {c['content']['raw']}"
            for c in comments
            if c.get("content", {}).get("raw")
        ) or "No comments."

    @mcp.tool()
    async def bitbucket_create_pr_comment(repo_slug: str, pr_id: int, content: str) -> dict:
        """Post a comment on a pull request."""
        ws = config.bitbucket_workspace
        return await client.post(
            f"/repositories/{ws}/{repo_slug}/pullrequests/{pr_id}/comments",
            json={"content": {"raw": content}},
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_mcp_tools.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Write `src/mcp_server/server.py`**

```python
import asyncio

from mcp.server.fastmcp import FastMCP

from src.mcp_server import tools

PORT = 7390


async def main() -> None:
    mcp = FastMCP("bitbucket-pr", host="0.0.0.0", port=PORT)
    tools.register(mcp)
    await mcp.run_streamable_http_async()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 7: Commit**

```bash
git add src/mcp_server/ tests/test_mcp_tools.py
git commit -m "Add Bitbucket PR-only MCP server"
```

---

## Task 7: Bot poller filtering logic

**Files:**
- Create: `src/bot/__init__.py`
- Create: `src/bot/poller.py`
- Create: `tests/test_poller.py`

**Interfaces:**
- Consumes: `src.db.is_reviewed` (Task 3).
- Produces: `src.bot.poller.filter_open(prs: list[dict]) -> list[dict]`, `src.bot.poller.filter_recent(prs: list[dict], now: datetime, window_minutes: int) -> list[dict]`, `src.bot.poller.filter_unreviewed(prs: list[dict], db_path: str, repo_slug: str) -> list[dict]`.

- [ ] **Step 1: Write `src/bot/__init__.py`**

```bash
mkdir -p src/bot
touch src/bot/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/test_poller.py`:
```python
from datetime import datetime, timezone

from src.bot.poller import filter_open, filter_recent, filter_unreviewed
from src.db import init_db, mark_reviewed


def test_filter_open_keeps_only_open():
    prs = [{"id": 1, "state": "OPEN"}, {"id": 2, "state": "MERGED"}, {"id": 3, "state": "DECLINED"}]
    result = filter_open(prs)
    assert [pr["id"] for pr in result] == [1]


def test_filter_recent_keeps_prs_inside_window():
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prs = [
        {"id": 1, "created_on": "2026-09-04T11:55:00.000000Z"},  # 5 min ago - inside
        {"id": 2, "created_on": "2026-09-04T11:30:00.000000Z"},  # 30 min ago - outside
    ]
    result = filter_recent(prs, now, window_minutes=10)
    assert [pr["id"] for pr in result] == [1]


def test_filter_recent_boundary_is_inclusive():
    now = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
    prs = [{"id": 1, "created_on": "2026-09-04T11:50:00.000000Z"}]  # exactly 10 min ago
    result = filter_recent(prs, now, window_minutes=10)
    assert [pr["id"] for pr in result] == [1]


def test_filter_unreviewed_skips_already_reviewed(tmp_path):
    db_path = str(tmp_path / "reviewed.db")
    init_db(db_path)
    mark_reviewed(db_path, "my-repo", 1, "2026-09-04T00:00:00+00:00")
    prs = [{"id": 1}, {"id": 2}]
    result = filter_unreviewed(prs, db_path, "my-repo")
    assert [pr["id"] for pr in result] == [2]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_poller.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bot.poller'`

- [ ] **Step 4: Write `src/bot/poller.py`**

```python
from datetime import datetime, timedelta

from src.db import is_reviewed


def filter_open(prs: list[dict]) -> list[dict]:
    """Keep only PRs in the OPEN state."""
    return [pr for pr in prs if pr.get("state") == "OPEN"]


def filter_recent(prs: list[dict], now: datetime, window_minutes: int) -> list[dict]:
    """Keep only PRs whose created_on falls within window_minutes of now."""
    cutoff = now - timedelta(minutes=window_minutes)
    recent = []
    for pr in prs:
        created_on = datetime.fromisoformat(pr["created_on"].replace("Z", "+00:00"))
        if created_on >= cutoff:
            recent.append(pr)
    return recent


def filter_unreviewed(prs: list[dict], db_path: str, repo_slug: str) -> list[dict]:
    """Keep only PRs not already recorded as reviewed."""
    return [pr for pr in prs if not is_reviewed(db_path, repo_slug, pr["id"])]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_poller.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/bot/__init__.py src/bot/poller.py tests/test_poller.py
git commit -m "Add bot poller filtering logic"
```

---

## Task 8: Bot scheduler loop

**Files:**
- Create: `src/bot/main.py`
- Create: `tests/test_bot_main.py`

**Interfaces:**
- Consumes: `src.bitbucket_client.BitbucketClient`, `src.config.Config`, `src.bot.poller.{filter_open,filter_recent,filter_unreviewed}`, `src.db.{init_db,mark_reviewed}`, `src.review_runner.{run_review,write_mcp_config}` (Tasks 1, 3, 4, 5, 7).
- Produces: `src.bot.main.resolve_repo_slugs(client, workspace: str, project_keys: list[str]) -> list[str]`, `src.bot.main.run_cycle(config: Config, client: BitbucketClient) -> None`, `src.bot.main.main() -> None`.

- [ ] **Step 1: Write the failing tests**

`tests/test_bot_main.py`:
```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.bot.main import resolve_repo_slugs, run_cycle
from src.config import Config
from src.db import init_db, is_reviewed


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

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {"values": [{"id": 1, "state": "OPEN", "created_on": recent_created}]}
        if path.endswith("/comments"):
            return {"values": []}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        mock_run_review.return_value = {"result": "ok"}
        await run_cycle(config, client)

    mock_run_review.assert_called_once()
    assert is_reviewed(config.db_path, "repo-a", 1) is True


@pytest.mark.asyncio
async def test_run_cycle_skips_already_reviewed_pr(tmp_path):
    config = _config(tmp_path)
    init_db(config.db_path)
    from src.db import mark_reviewed

    now = datetime.now(timezone.utc)
    recent_created = now.isoformat().replace("+00:00", "Z")
    mark_reviewed(config.db_path, "repo-a", 1, now.isoformat())

    client = AsyncMock()

    async def fake_get(path, params=None):
        if path == f"/repositories/{config.bitbucket_workspace}":
            return {"values": [{"slug": "repo-a"}]}
        if path.endswith("/pullrequests"):
            return {"values": [{"id": 1, "state": "OPEN", "created_on": recent_created}]}
        raise AssertionError(f"unexpected path {path}")

    client.get.side_effect = fake_get

    with patch("src.bot.main.run_review") as mock_run_review:
        await run_cycle(config, client)

    mock_run_review.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bot_main.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.bot.main'`

- [ ] **Step 3: Write `src/bot/main.py`**

```python
import asyncio
import logging
from datetime import datetime, timezone

from src.bitbucket_client import BitbucketClient
from src.bot.poller import filter_open, filter_recent, filter_unreviewed
from src.config import Config
from src.db import init_db, mark_reviewed
from src.review_runner import run_review, write_mcp_config

logger = logging.getLogger(__name__)


async def resolve_repo_slugs(client: BitbucketClient, workspace: str, project_keys: list[str]) -> list[str]:
    """Resolve Bitbucket Project keys into member repo slugs."""
    slugs: list[str] = []
    for key in project_keys:
        data = await client.get(f"/repositories/{workspace}", params={"q": f'project.key="{key}"'})
        slugs.extend(repo["slug"] for repo in data.get("values", []))
    return slugs


def _format_comments(comments_data: dict) -> str:
    return "\n".join(
        f"[{c['user']['display_name']}] {c['content']['raw']}"
        for c in comments_data.get("values", [])
        if c.get("content", {}).get("raw")
    )


async def run_cycle(config: Config, client: BitbucketClient) -> None:
    """Run one poll cycle: find newly-opened PRs across configured projects and review them."""
    now = datetime.now(timezone.utc)
    repo_slugs = await resolve_repo_slugs(client, config.bitbucket_workspace, config.project_keys)
    for repo_slug in repo_slugs:
        data = await client.get(
            f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests",
            params={"state": "OPEN"},
        )
        prs = filter_open(data.get("values", []))
        prs = filter_recent(prs, now, config.poll_interval_minutes)
        prs = filter_unreviewed(prs, config.db_path, repo_slug)
        for pr in prs:
            comments_data = await client.get(
                f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr['id']}/comments"
            )
            comments_text = _format_comments(comments_data)
            try:
                run_review(repo_slug, pr["id"], comments_text, config.mcp_config_path)
                mark_reviewed(config.db_path, repo_slug, pr["id"], now.isoformat())
            except Exception:
                logger.exception("Review failed for %s PR #%s", repo_slug, pr["id"])


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    config = Config()
    init_db(config.db_path)
    write_mcp_config(config.mcp_config_path, config.mcp_url)
    client = BitbucketClient(config)
    try:
        while True:
            await run_cycle(config, client)
            await asyncio.sleep(config.poll_interval_minutes * 60)
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_bot_main.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/bot/main.py tests/test_bot_main.py
git commit -m "Add bot scheduler loop"
```

---

## Task 9: Web app (manual review trigger)

**Files:**
- Create: `src/web/__init__.py`
- Create: `src/web/app.py`
- Create: `tests/test_web_app.py`

**Interfaces:**
- Consumes: `src.bitbucket_client.BitbucketClient`, `src.config.Config`, `src.db.{init_db,mark_reviewed}`, `src.pr_url.parse_pr_url`, `src.review_runner.{run_review,write_mcp_config}` (Tasks 1, 2, 3, 4, 5).
- Produces: `src.web.app.create_app(config: Config, client: BitbucketClient | None = None) -> FastAPI`, `src.web.app.app_factory() -> FastAPI`.

- [ ] **Step 1: Write `src/web/__init__.py`**

```bash
mkdir -p src/web
touch src/web/__init__.py
```

- [ ] **Step 2: Write the failing tests**

`tests/test_web_app.py`:
```python
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
    fake_client.get.return_value = {"values": []}
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
    assert is_reviewed(config.db_path, "my-repo", 5) is True
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_web_app.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.web.app'`

- [ ] **Step 4: Write `src/web/app.py`**

```python
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.bitbucket_client import BitbucketClient
from src.config import Config
from src.db import init_db, mark_reviewed
from src.pr_url import parse_pr_url
from src.review_runner import run_review, write_mcp_config

_FORM_HTML = """<!doctype html>
<html>
<head><title>Bitbucket PR Review</title></head>
<body>
<h1>Review a Bitbucket PR</h1>
<form id="review-form">
  <input type="text" id="pr_url" name="pr_url"
         placeholder="https://bitbucket.org/{workspace}/{repo}/pull-requests/{id}" size="80">
  <button type="submit">Review</button>
</form>
<pre id="result"></pre>
<script>
document.getElementById("review-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const pr_url = document.getElementById("pr_url").value;
  document.getElementById("result").textContent = "Reviewing...";
  const res = await fetch("/review", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({pr_url}),
  });
  const data = await res.json();
  document.getElementById("result").textContent = JSON.stringify(data, null, 2);
});
</script>
</body>
</html>
"""


class ReviewRequest(BaseModel):
    pr_url: str


def _format_comments(comments_data: dict) -> str:
    return "\n".join(
        f"[{c['user']['display_name']}] {c['content']['raw']}"
        for c in comments_data.get("values", [])
        if c.get("content", {}).get("raw")
    )


def create_app(config: Config, client: BitbucketClient | None = None) -> FastAPI:
    """Build the FastAPI app, wiring in the given Config (and optionally an injected client for tests)."""
    init_db(config.db_path)
    write_mcp_config(config.mcp_config_path, config.mcp_url)
    client = client or BitbucketClient(config)
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def index() -> str:
        return _FORM_HTML

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok"}

    @app.post("/review")
    async def review(req: ReviewRequest) -> dict:
        try:
            repo_slug, pr_id = parse_pr_url(req.pr_url, config.bitbucket_workspace)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        comments_data = await client.get(
            f"/repositories/{config.bitbucket_workspace}/{repo_slug}/pullrequests/{pr_id}/comments"
        )
        comments_text = _format_comments(comments_data)
        result = run_review(repo_slug, pr_id, comments_text, config.mcp_config_path)
        mark_reviewed(config.db_path, repo_slug, pr_id, datetime.now(timezone.utc).isoformat())
        return {"status": "reviewed", "repo_slug": repo_slug, "pr_id": pr_id, "claude_result": result}

    return app


def app_factory() -> FastAPI:
    return create_app(Config())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_web_app.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add src/web/ tests/test_web_app.py
git commit -m "Add web app for on-demand PR review"
```

---

## Task 10: Docker packaging

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`
- Modify: `.gitignore` (add `.env`, `__pycache__/`, `*.pyc`, `.pytest_cache/`)
- Create: `README.md`

**Interfaces:**
- Consumes: everything from Tasks 1-9 (entrypoints `src.mcp_server.server`, `src.bot.main`, `src.web.app:app_factory`).
- Produces: a runnable `docker-compose.yml` with services `mcp`, `bot`, `web`.

- [ ] **Step 1: Write `.gitignore` additions**

```
.env
__pycache__/
*.pyc
.pytest_cache/
*.egg-info/
```

- [ ] **Step 2: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g @anthropic-ai/claude-code && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY src/ src/

ENTRYPOINT ["python"]
```

- [ ] **Step 3: Write `.env.example`**

```
BITBUCKET_EMAIL=you@example.com
BITBUCKET_API_TOKEN=your-atlassian-api-token
BITBUCKET_WORKSPACE=your-workspace-slug
PROJECT_KEYS=PROJ1,PROJ2
POLL_INTERVAL_MINUTES=10
MCP_URL=http://mcp:7390/mcp
MCP_CONFIG_PATH=/app/mcp-config.json
DB_PATH=/data/reviewed_prs.db
```

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
services:
  mcp:
    build: .
    container_name: bitbucket-pr-mcp
    env_file: .env
    command: ["-m", "src.mcp_server.server"]
    ports:
      - "7390:7390"
    restart: unless-stopped

  bot:
    build: .
    container_name: bitbucket-pr-bot
    env_file: .env
    command: ["-m", "src.bot.main"]
    volumes:
      - reviewed-prs:/data
      - claude-auth:/root/.claude
    depends_on:
      - mcp
    restart: unless-stopped

  web:
    build: .
    container_name: bitbucket-pr-web
    env_file: .env
    command: ["-m", "uvicorn", "src.web.app:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8080"]
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - reviewed-prs:/data
      - claude-auth:/root/.claude
    depends_on:
      - mcp
    restart: unless-stopped

volumes:
  reviewed-prs:
  claude-auth:
```

- [ ] **Step 5: Verify the compose file is valid**

Run: `docker compose config --quiet`
Expected: exits 0 with no output (or, if Docker isn't available in this environment, `python -c "import yaml; yaml.safe_load(open('docker-compose.yml'))"` parses without error).

- [ ] **Step 6: Write `README.md`**

```markdown
# Bitbucket PR Review Bot

Polls Bitbucket every `POLL_INTERVAL_MINUTES` for newly-opened PRs across the
configured Bitbucket Projects and reviews them with headless Claude. Also
serves a localhost web page (`http://localhost:8080`) to trigger an
immediate review by pasting a PR URL.

## Setup

1. Copy `.env.example` to `.env` and fill in `BITBUCKET_EMAIL`,
   `BITBUCKET_API_TOKEN`, `BITBUCKET_WORKSPACE`, and `PROJECT_KEYS`.
2. Build the image: `docker compose build`
3. One-time Claude login (uses your Claude Pro subscription, not an API key):
   ```
   docker compose run --rm bot claude login
   ```
   Follow the OAuth flow in your browser. The resulting credentials persist
   on the `claude-auth` volume, so this is only needed once.
4. Start everything: `docker compose up -d`
5. Open `http://localhost:8080` to trigger a review manually, or wait for
   the next poll cycle for newly-opened PRs to be reviewed automatically.

## Design

See `docs/superpowers/specs/2026-09-04-bitbucket-pr-review-bot-design.md`.
```

- [ ] **Step 7: Run the full test suite**

Run: `pytest -v`
Expected: all tests across `tests/test_config.py`, `tests/test_pr_url.py`, `tests/test_db.py`, `tests/test_bitbucket_client.py`, `tests/test_review_runner.py`, `tests/test_mcp_tools.py`, `tests/test_poller.py`, `tests/test_bot_main.py`, `tests/test_web_app.py` PASS.

- [ ] **Step 8: Commit**

```bash
git add Dockerfile docker-compose.yml .env.example .gitignore README.md
git commit -m "Add Docker packaging and setup docs"
```
