# Bitbucket PR Review Bot — Design

## Purpose

Automatically review newly-opened Bitbucket pull requests using Claude in
headless mode, on a configurable poll interval, plus an on-demand web
trigger for immediate review without waiting for the next poll cycle. If
Claude reports a usage-limit failure, the same review is retried with
headless Kiro CLI as a fallback agent, so reviews don't silently stop when
the Claude Pro quota is exhausted.

## Non-goals

- Re-reviewing a PR when new commits are pushed after its initial review
  (out of scope — only PR *creation* is polled for).
- Replying to individual existing comment threads (existing comments are
  passed as context to the review, not replied to individually).
- Auth/login on the web trigger (assumed internal/localhost use).
- Multi-workspace support (single Bitbucket workspace per deployment).
- General-purpose multi-agent orchestration — Kiro is strictly a fallback
  for Claude's usage limit, not a load-balanced second reviewer or a retry
  mechanism for other failure types.

## Architecture

Three services via `docker-compose`, sharing one Docker volume for SQLite
state and Docker volumes for persisted CLI auth (Claude and Kiro each get
their own):

```
                 ┌──────────────┐
   poll every    │     bot      │  headless claude -p  ┌─────────────┐
   N minutes ──▶ │  (scheduler) │ ───────────────────▶ │     mcp     │
                 └──────┬───────┘  (Kiro fallback)      │ (Bitbucket  │
                        │                                │  PR tools) │
   manual dump   ┌──────▼───────┐  headless claude -p    └─────────────┘
   via HTTP  ──▶ │     web      │ ───────────────────▶        ▲
                 │  (FastAPI)   │  (Kiro fallback)             │
                 └──────┬───────┘                              │
                        │                                      │
                 shared SQLite (reviewed_prs)          Bitbucket Cloud API
                 shared claude-auth / kiro-auth / kiro-aws-sso volumes
```

- **mcp**: Bitbucket PR-only MCP server (FastMCP, streamable-HTTP transport,
  same pattern as the reference `local-mcp` project). Exposes only PR-related
  tools.
- **bot**: background scheduler, polls Bitbucket on an interval, decides
  which PRs need review, invokes headless Claude for each (falling back to
  headless Kiro on a Claude usage-limit failure).
- **web**: FastAPI app with a manual "review this PR now" endpoint/form,
  invokes the same review path as the bot (Claude first, Kiro fallback).

Both `bot` and `web` share:
- A SQLite file (`/data/reviewed_prs.db`) recording which `(repo_slug,
  pr_id)` pairs have been reviewed, so a web-triggered review also prevents
  the bot from re-reviewing the same PR on its next poll.
- A `claude-auth` volume holding the Claude Code CLI's persisted OAuth
  credentials (Pro subscription login, not an API key — see Auth section).
- `kiro-auth` and `kiro-aws-sso` volumes holding Kiro CLI's persisted
  session/config (`~/.kiro`) and auth token cache (`~/.aws/sso/cache`),
  used only when the Claude fallback path is exercised.

## Component: `mcp` — Bitbucket PR MCP server

Subset of the reference `local-mcp` Bitbucket tool module, trimmed to
PR-related tools only:

- `bitbucket_list_repos` (used to resolve Project key → repo slugs)
- `bitbucket_get_repo`
- `bitbucket_list_prs`
- `bitbucket_get_pr`
- `bitbucket_get_pr_diff`
- `bitbucket_list_pr_comments`
- `bitbucket_create_pr_comment`

Same auth pattern as reference: HTTP Basic with `BITBUCKET_EMAIL` +
`BITBUCKET_API_TOKEN` against `api.bitbucket.org/2.0`. Runs FastMCP with
`run_streamable_http_async()` on a single port (e.g. `7390`).

## Component: `bot` — scheduler / poller

Runs an asyncio loop, sleeping `POLL_INTERVAL_MINUTES` (default 10) between
cycles. Each cycle:

1. For each configured Bitbucket Project key in `PROJECT_KEYS`
   (comma-separated), resolve member repos via
   `GET /repositories/{workspace}?q=project.key="X"`.
2. For each repo, list PRs with `state=OPEN` (state filter happens here —
   merged/declined PRs are never fetched for review).
3. Filter to PRs whose `created_on` falls within the last
   `POLL_INTERVAL_MINUTES`.
4. For each surviving PR, check the SQLite `reviewed_prs` table on
   `(repo_slug, pr_id)`; skip if already present.
5. Fetch existing PR comments (for context) and invoke the **review
   runner** (below).
6. On successful run (subprocess exit 0), insert
   `(repo_slug, pr_id, reviewed_at)` into `reviewed_prs`.

## Component: `web` — on-demand trigger

Minimal FastAPI app, served on localhost:

- `GET /` — single HTML page: one URL text input (paste full Bitbucket PR
  URL, e.g. `https://bitbucket.org/{workspace}/{repo_slug}/pull-requests/{pr_id}`)
  + a submit button. On submit, posts to `/review` and shows the result
  (success/error text) on the same page.
- `POST /review` — accepts `{ "pr_url": "..." }`. Parses `repo_slug` and
  `pr_id` out of the URL (400 if it doesn't match the expected Bitbucket PR
  URL shape). Always invokes the review runner immediately, regardless of
  `reviewed_prs` state (manual re-review is intentional — e.g. after
  pushing new commits). On success, still writes/updates the
  `reviewed_prs` row so the bot doesn't duplicate it on its next poll.
- `GET /health` — liveness check.
- No authentication (localhost-only use, per requirements).

## Review runner (shared logic)

A single function, used by both `bot` and `web`, that:

1. Builds a prompt containing: repo slug, PR id, and existing PR comments
   (if any) as context — so the reviewing agent doesn't repeat points
   already raised and can build on them.
2. Spawns headless Claude:
   ```
   claude -p "<prompt>" \
     --mcp-config /app/mcp-config.json \
     --allowedTools "mcp__bitbucket-pr__bitbucket_get_pr,mcp__bitbucket-pr__bitbucket_get_pr_diff,mcp__bitbucket-pr__bitbucket_list_pr_comments,mcp__bitbucket-pr__bitbucket_create_pr_comment" \
     --output-format json
   ```
   `mcp-config.json` points at the `mcp` service over HTTP
   (`MCP_URL`, e.g. `http://mcp:7390/mcp`) — not a stdio-spawned subprocess.
3. Claude itself fetches the diff/comments and posts the review comment via
   `bitbucket_create_pr_comment` — the runner does not construct or post the
   comment text itself.
4. Checks whether the Claude invocation hit a **usage-limit failure**: a
   non-zero exit, or an exit-0 result whose JSON `result` text contains a
   known limit/quota phrase. Any other failure propagates immediately
   (`subprocess.CalledProcessError`) without falling back.
5. On a usage-limit failure, retries the same prompt with headless Kiro
   CLI:
   ```
   kiro-cli chat "<prompt>" \
     --agent pr-reviewer \
     --no-interactive \
     --output-format text
   ```
   using the `pr-reviewer` agent config (`.kiro/agents/pr-reviewer.json`),
   which declares `mcpServers.bitbucket-pr` pointed at the same `mcp`
   service and an `allowedTools` list equivalent to Claude's
   `--allowedTools`. This file is regenerated at startup with the literal
   `MCP_URL` value (Kiro CLI's `${VAR}` expansion does not apply to a
   remote MCP server's `url` field — a static placeholder there fails at
   runtime with `relative URL without a base`). If the Kiro fallback also
   fails, that failure propagates.
6. Runner checks the final exit code and result for logging/status; does
   not retry beyond the single Claude→Kiro fallback.

## Auth

### Bitbucket
HTTP Basic auth: `BITBUCKET_EMAIL` + `BITBUCKET_API_TOKEN`, same as the
reference `local-mcp` project.

### Claude (Pro subscription, no API key)
The user has a Claude Pro subscription, not an API key — headless Claude
must authenticate via OAuth login, which requires a browser and can't
complete unattended inside a fresh container.

Setup: a named `claude-auth` volume is mounted at Claude's config directory
in both `bot` and `web` containers. On first-time setup, run:

```
docker compose run --rm bot claude login
```

Complete the OAuth flow once; the resulting token persists on the
`claude-auth` volume, so subsequent container restarts don't require
re-login.

**Known constraint**: Pro-plan usage is a personal quota shared with the
user's own interactive use of Claude. Frequent polling (every 10 min)
combined with manual on-demand reviews counts against that same quota and
could hit rate limits under heavy PR volume. This is precisely the
constraint the Kiro fallback exists to mitigate — see below.

### Kiro CLI (fallback agent)
Kiro CLI persists its session/config under `~/.kiro` and its auth token
cache under `~/.aws/sso/cache` (`~/.aws`). Like Claude, headless Kiro needs
a one-time interactive login before it can run unattended.

Setup: named `kiro-auth` (`/root/.kiro`) and `kiro-aws-sso` (`/root/.aws`)
volumes are mounted in both `bot` and `web` containers. On first-time
setup, run:

```
docker compose run --rm --entrypoint kiro-cli bot login --use-device-flow
```

`--use-device-flow` is required — the container can't complete a browser
loopback redirect, so this prints a code and URL to complete on another
device instead. Complete the device/browser auth flow once; credentials
persist on those volumes, so subsequent container restarts don't require
re-login. Kiro is only invoked when Claude reports a usage-limit failure,
so its quota is consumed far less frequently than Claude's.

## State: SQLite schema

```sql
CREATE TABLE reviewed_prs (
    repo_slug   TEXT NOT NULL,
    pr_id       INTEGER NOT NULL,
    reviewed_at TEXT NOT NULL,
    PRIMARY KEY (repo_slug, pr_id)
);
```

File lives at `/data/reviewed_prs.db` on a shared Docker volume mounted by
both `bot` and `web`.

## Config (environment variables)

| Var | Used by | Description |
|---|---|---|
| `BITBUCKET_EMAIL` | mcp, bot | Atlassian account email |
| `BITBUCKET_API_TOKEN` | mcp, bot | Atlassian API token |
| `BITBUCKET_WORKSPACE` | mcp, bot | Bitbucket workspace slug |
| `PROJECT_KEYS` | bot | Comma-separated Bitbucket Project keys to watch |
| `POLL_INTERVAL_MINUTES` | bot | Poll cycle interval, default `10` |
| `MCP_URL` | bot, web | URL of the `mcp` service, e.g. `http://mcp:7390/mcp` |
| `MCP_CONFIG_PATH` | bot, web | Path to write Claude's MCP config file, default `/app/mcp-config.json` |
| `KIRO_MCP_CONFIG_PATH` | bot, web | Path to the `pr-reviewer` Kiro agent config, regenerated at startup with the literal `MCP_URL`, default `/app/.kiro/agents/pr-reviewer.json` |
| `KIRO_AGENT_NAME` | bot, web | Kiro agent config name to use for the fallback review, default `pr-reviewer` |


## Testing

- Unit tests for the poller's filtering logic (state=OPEN, created_on
  window, dedup against `reviewed_prs`) using a fake Bitbucket client.
- Unit tests for the MCP tool subset (mirroring the reference project's
  tool tests, trimmed to PR tools).
- Unit tests for `review_runner.run_review`'s Claude→Kiro fallback: a
  generic Claude failure propagates without invoking Kiro; a usage-limit
  failure (via non-zero exit or exit-0 result text) invokes Kiro and
  returns its result; a Kiro failure after a Claude usage-limit failure
  propagates. All via mocked `subprocess.run`, no real CLI invocations.
- Manual/integration check: run `docker compose up`, create a test PR,
  confirm a review comment appears within one poll cycle; hit `POST
  /review` directly and confirm immediate review + `reviewed_prs` row.
