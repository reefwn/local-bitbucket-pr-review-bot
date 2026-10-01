# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Project purpose

Bitbucket PR Review Bot. Polls Bitbucket every `POLL_INTERVAL_MINUTES` for
newly-opened pull requests across configured Bitbucket Projects and reviews
them with a headless LLM CLI. Also serves a localhost web page
(`http://localhost:8080`) to trigger an immediate review by pasting a PR
URL.

The primary review agent is headless **Claude Code** (`claude -p ...`).
When Claude's usage limit is hit, the bot falls back to device-authenticated
**Codex** (`codex exec ...`), then headless **Kiro CLI** (`kiro-cli chat ...`),
then headless **Cursor Agent** (`agent -p ...`) when earlier providers exhaust
their quota. See "Review provider fallback" below.

Full design doc: `docs/superpowers/specs/2026-09-04-bitbucket-pr-review-bot-design.md`.

## Architecture

Three services via `docker-compose`, sharing one Docker volume for SQLite
state and Docker volumes for persisted CLI auth (`claude-auth`, `codex-auth`, `kiro-auth`,
`kiro-aws-sso`, `kiro-data`, `cursor-auth`):

```
                 ┌──────────────┐
   poll every    │     bot      │  headless claude -p  ┌─────────────┐
   N minutes ──▶ │  (scheduler) │ ───────────────────▶ │     mcp     │
                 └──────┬───────┘  (Codex/Kiro fallback)│ (Bitbucket  │
                        │                                │  PR tools) │
   manual dump   ┌──────▼───────┐  headless claude -p    └─────────────┘
   via HTTP  ──▶ │     web      │ ───────────────────▶        ▲
                 │  (FastAPI)   │  (Codex/Kiro fallback)       │
                 └──────┬───────┘                              │
                        │                                      │
                 shared SQLite (reviewed_prs)          Bitbucket Cloud API
                 shared claude-auth / codex-auth / kiro-auth / kiro-aws-sso / kiro-data / cursor-auth
```

- `src/mcp_server/` — FastMCP server exposing a Bitbucket PR-only tool
  subset (`bitbucket_get_pr`, `bitbucket_get_pr_diff`,
  `bitbucket_list_pr_comments`, `bitbucket_create_pr_comment`,
  `bitbucket_approve_pr`, `bitbucket_request_changes_pr`), served over
  streamable HTTP.
- `src/bot/` — `poller.py` (pure filtering logic: open state, recency
  window, dedup against `reviewed_prs`) and `main.py` (asyncio scheduler
  loop).
- `src/web/app.py` — FastAPI app with a manual "review this PR now" form
  and `POST /review` endpoint, using the same review path as the bot.
- `src/review_runner.py` — shared logic: builds the review prompt, writes
  MCP config files, and invokes the headless CLI (Claude first, then Codex,
  then Cursor, then Kiro on usage-limit failures).
- `src/bitbucket_client.py` — thin async HTTP client for the Bitbucket
  Cloud REST API (HTTP Basic auth via `BITBUCKET_EMAIL` +
  `BITBUCKET_API_TOKEN`).
- `src/db.py` — SQLite `reviewed_prs` table, keyed on
  `(repo_slug, pr_id)`, storing the reviewed commit hash so new commits
  re-enter the review queue.
- `src/config.py` — `Config` dataclass loading all environment variables
  (via `python-dotenv`).

## Review provider fallback

`review_runner.run_review()`:

1. Runs headless Claude:
   ```
   claude -p "<prompt>" --mcp-config <claude-mcp-config-path> \
     --allowedTools "mcp__bitbucket-pr__..." --output-format json
   ```
2. Treats the run as a **usage-limit failure** (not a generic error) when
   either:
   - the subprocess exits non-zero, or
   - it exits 0 but the JSON `result` text matches known limit phrases
     (e.g. "usage limit", "session limit", "rate limit").
3. On a usage-limit failure, falls back to device-authenticated Codex:
   ```
   codex exec --json --skip-git-repo-check \
     --dangerously-bypass-approvals-and-sandbox \
     --config 'mcp_servers.bitbucket-pr.url="http://mcp:7390/mcp"' "<prompt>"
   ```
   The MCP URL is passed as a per-invocation TOML override, avoiding changes to
   `~/.codex/config.toml` in the volume holding the device-login credentials.
4. If Codex reports a usage-limit failure, falls back to headless Cursor Agent:
   ```
   agent -p "<prompt>" --trust --approve-mcps --force \
     --output-format json --workspace /app
   ```
   using project MCP config at `.cursor/mcp.json` (regenerated at startup via
   `write_cursor_mcp_config()` with the literal `MCP_URL`). Authenticate with
   `agent login` (persisted on the `cursor-auth` volume).
5. If Cursor reports a usage-limit failure, falls back to headless Kiro CLI:
   ```
   kiro-cli chat "<prompt>" --agent pr-reviewer --no-interactive \
     --output-format text
   ```
   using the `pr-reviewer` agent config (`.kiro/agents/pr-reviewer.json`),
   which points at the same `mcp` service and allows the equivalent
   Bitbucket PR tool set. `write_kiro_mcp_config()` regenerates this file
   at startup with the literal `MCP_URL` value — Kiro CLI's `${VAR}`
   expansion does not apply to a remote MCP server's `url` field, so a
   static placeholder there fails at runtime.
6. Any other (non-usage-limit) failure from an earlier provider propagates
   immediately — the fallbacks are for exhausted quota, not general retries.

This keeps the primary review path on Claude (per the original design), uses
Codex when Claude genuinely cannot run, and only spends Cursor or Kiro quota when
earlier reviewers are exhausted.

The web dashboard's **Review providers** modal persists a full provider order
in the shared SQLite database. Each new manual or scheduled review reads that
order, so the first entry is primary and later entries are usage-limit-only
fallbacks.

## Dev commands

```bash
# install (editable, with test deps)
pip install -e ".[test]"

# run tests
pytest

# run a single test file
pytest tests/test_review_runner.py

# build + run full stack
docker compose build
docker compose up -d

# one-time Claude login (persists on claude-auth volume)
docker compose run --rm --entrypoint claude bot auth login

# one-time Codex device login (persists on codex-auth volume)
docker compose up -d
docker compose exec bot codex login --device-auth

# one-time Kiro login (persists on kiro-data volume at
# /root/.local/share/kiro-cli — NOT ~/.kiro or ~/.aws despite those names;
# must use exec against the running bot container, not run --rm, or the
# credentials are destroyed when the throwaway container exits)
docker compose exec bot kiro-cli login --use-device-flow

# one-time Cursor Agent login (cursor-auth volume)
docker compose exec bot agent login
```

## Conventions

- Python 3.11+, async-first (`httpx`, `asyncio`) for I/O; synchronous
  `subprocess.run` for the headless CLI calls (run via `asyncio.to_thread`
  from async call sites).
- Config is centralized in `src/config.py`'s `Config` dataclass — don't
  read `os.getenv` elsewhere; add new fields there and to `.env.example`.
- `review_runner.py` functions are pure where possible (`build_prompt`,
  `write_mcp_config`) so they're trivially unit-testable without
  subprocess mocking.
- Tests mock `subprocess.run` rather than invoking real CLIs; keep new CLI
  invocations similarly mockable (don't shell out via `os.system` or
  string-interpolated commands).
- No retries baked into `run_review` beyond the configured provider-order fallback —
  surface failures rather than silently swallowing them.
- Never decline or close a PR from the review path; only approve or
  request changes.
