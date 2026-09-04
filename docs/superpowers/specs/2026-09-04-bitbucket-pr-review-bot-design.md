# Bitbucket PR Review Bot — Design

## Purpose

Automatically review newly-opened Bitbucket pull requests using Claude in
headless mode, on a configurable poll interval, plus an on-demand web
trigger for immediate review without waiting for the next poll cycle.

## Non-goals

- Re-reviewing a PR when new commits are pushed after its initial review
  (out of scope — only PR *creation* is polled for).
- Replying to individual existing comment threads (existing comments are
  passed as context to the review, not replied to individually).
- Auth/login on the web trigger (assumed internal/localhost use).
- Multi-workspace support (single Bitbucket workspace per deployment).

## Architecture

Three services via `docker-compose`, sharing one Docker volume for SQLite
state and one Docker volume for persisted Claude CLI auth:

```
                 ┌──────────────┐
   poll every    │     bot      │  headless claude -p  ┌─────────────┐
   N minutes ──▶ │  (scheduler) │ ───────────────────▶ │     mcp     │
                 └──────┬───────┘   MCP over HTTP       │ (Bitbucket  │
                        │                                │  PR tools) │
   manual dump   ┌──────▼───────┐  headless claude -p    └─────────────┘
   via HTTP  ──▶ │     web      │ ───────────────────▶        ▲
                 │  (FastAPI)   │                              │
                 └──────┬───────┘                              │
                        │                                      │
                 shared SQLite (reviewed_prs)          Bitbucket Cloud API
                 shared claude-auth volume
```

- **mcp**: Bitbucket PR-only MCP server (FastMCP, streamable-HTTP transport,
  same pattern as the reference `local-mcp` project). Exposes only PR-related
  tools.
- **bot**: background scheduler, polls Bitbucket on an interval, decides
  which PRs need review, invokes headless Claude for each.
- **web**: FastAPI app with a manual "review this PR now" endpoint/form,
  invokes the same review path as the bot, immediately.

Both `bot` and `web` share:
- A SQLite file (`/data/reviewed_prs.db`) recording which `(repo_slug,
  pr_id)` pairs have been reviewed, so a web-triggered review also prevents
  the bot from re-reviewing the same PR on its next poll.
- A `claude-auth` volume holding the Claude Code CLI's persisted OAuth
  credentials (Pro subscription login, not an API key — see Auth section).

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

Minimal FastAPI app:

- `GET /` — HTML form: repo slug + PR id fields (or a single "paste PR URL"
  field, parsed into repo slug + PR id).
- `POST /review` — same fields as JSON body. Always invokes the review
  runner immediately, regardless of `reviewed_prs` state (manual re-review
  is intentional — e.g. after pushing new commits). On success, still
  writes/updates the `reviewed_prs` row so the bot doesn't duplicate it on
  its next poll.
- `GET /health` — liveness check.
- No authentication (internal/localhost use only, per requirements).

## Review runner (shared logic)

A single function, used by both `bot` and `web`, that:

1. Builds a prompt containing: repo slug, PR id, and existing PR comments
   (if any) as context — so Claude doesn't repeat points already raised and
   can build on them.
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
4. Runner checks the subprocess exit code and parses the JSON result for
   logging/status; does not retry on failure (surfaces the error).

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
could hit rate limits under heavy PR volume. Not a blocker for this design,
but worth monitoring.

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

## Testing

- Unit tests for the poller's filtering logic (state=OPEN, created_on
  window, dedup against `reviewed_prs`) using a fake Bitbucket client.
- Unit tests for the MCP tool subset (mirroring the reference project's
  tool tests, trimmed to PR tools).
- Manual/integration check: run `docker compose up`, create a test PR,
  confirm a review comment appears within one poll cycle; hit `POST
  /review` directly and confirm immediate review + `reviewed_prs` row.
