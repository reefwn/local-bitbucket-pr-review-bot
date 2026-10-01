# Bitbucket PR Review Bot

Shared guidance for every coding agent working in this repository. Provider-
specific notes may live in `CLAUDE.md` and `KIRO.md`; this file is the source
of truth for project behavior and engineering conventions.

## Purpose and architecture

The bot polls configured Bitbucket projects for newly opened pull requests and
posts a review. A FastAPI dashboard at `http://localhost:8080` also allows a
manual review by PR URL.

- `src/mcp_server/` exposes a deliberately narrow Bitbucket PR MCP server.
- `src/bot/` resolves repositories and runs the scheduled batch cycle.
- `src/web/app.py` provides the dashboard and configuration endpoints.
- `src/review_runner.py` builds prompts and invokes the provider CLIs.
- `src/db.py` stores review history and persisted application settings.

Docker Compose runs `mcp`, `bot`, and `web`. The bot and web services share
the SQLite volume and persistent auth volumes for Claude, Codex, and Kiro.

## Review providers

Supported providers are `claude`, `codex`, `kiro`, and `cursor`. Their priority is stored
in SQLite and configured from the dashboard's **Review providers** modal.
Every new manual or scheduled review reads that order:

1. The first provider is primary.
2. Later providers run only when the current provider reports a usage, session,
   rate, or quota limit.
3. Any other provider failure is surfaced; do not turn it into a silent retry.

The default order is Claude, Codex, Cursor, Kiro. Provider-order validation must require
each supported provider exactly once. Keep the dashboard API, database setting,
and `run_review()` behavior in sync when adding or removing a provider.

Never decline or close a pull request from the review path. A review must post
one summary comment and then approve or request changes.

## Commands

```bash
pip install -e ".[test]"
pytest
docker compose build
docker compose up -d
```

One-time provider authentication is persisted by Docker volumes:

```bash
docker compose run --rm --entrypoint claude bot auth login
docker compose exec bot codex login --device-auth
docker compose exec bot kiro-cli login --use-device-flow
docker compose exec bot agent login
```

## Engineering conventions

- Use Python 3.11+ with async I/O for network work. Run blocking provider CLIs
  through `asyncio.to_thread`.
- Keep environment-variable parsing in `src/config.py`; add new settings to
  `.env.example` when appropriate.
- Keep the MCP tool surface narrow and provider configurations aligned with it.
- Add or update tests for behavior changes. Mock `subprocess.run`; do not invoke
  real provider CLIs in tests.
- Preserve existing user changes in a dirty worktree and avoid destructive Git
  operations.
