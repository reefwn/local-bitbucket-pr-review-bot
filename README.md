# Bitbucket PR Review Bot

Polls Bitbucket every `POLL_INTERVAL_MINUTES` for newly-opened PRs across the
configured Bitbucket Projects and reviews them with headless Claude, falling
back to headless Kiro CLI if Claude's usage limit is hit. Also serves a
localhost web page (`http://localhost:8080`) to trigger an immediate review
by pasting a PR URL.

## Setup

1. Copy `.env.example` to `.env` and fill in `BITBUCKET_EMAIL`,
   `BITBUCKET_API_TOKEN`, `BITBUCKET_WORKSPACE`, and `PROJECT_KEYS`.
2. Build the image: `docker compose build`
3. One-time Claude login (uses your Claude Pro subscription, not an API key):
   ```
   docker compose run --rm --entrypoint claude bot auth login
   ```
   Follow the OAuth flow in your browser. The resulting credentials persist
   on the `claude-auth` volume, so this is only needed once. If the OAuth
   browser redirect can't reach the container cleanly, use `claude setup-token`
   instead to generate a token non-interactively for headless use.
4. One-time Codex login (used when Claude hits its usage limit):
   ```
   docker compose up -d
   docker compose exec bot codex login --device-auth
   ```
   The command displays a URL and device code. Complete that flow in a browser.
   Credentials persist in the `codex-auth` volume (`/root/.codex`), so this is
   only needed once. Codex is invoked with the same Bitbucket MCP server as the
   other reviewers.
5. One-time Kiro CLI login (used as the final fallback when Claude and Codex hit their
   usage limit). Start the stack first (`docker compose up -d`), then run
   login against the persistent `bot` container — **use `exec`, not
   `run --rm`**: `kiro-cli` stores its actual session/credentials under
   `/root/.local/share/kiro-cli` (not `~/.kiro` or `~/.aws`, despite what
   those directory names suggest), and a `run --rm` container's writable
   layer — and anything written to it — is destroyed the moment the
   command exits, so credentials never persist:
   ```
   docker compose exec bot kiro-cli login --use-device-flow
   ```
   If your organization uses AWS IAM Identity Center (not Builder ID /
   Google / GitHub), you'll likely need to specify it explicitly — ask
   your admin for the Start URL and region:
   ```
   docker compose exec bot kiro-cli login --license pro \
     --identity-provider https://your-start-url.awsapps.com/start/ \
     --region us-east-1 --use-device-flow
   ```
   Either form prints a code and URL — open the URL in a browser and
   confirm the code before the command exits. Credentials persist on the
   `kiro-data` volume (`/root/.local/share/kiro-cli`), so this is only
   needed once.
6. Start everything: `docker compose up -d`
7. Open `http://localhost:8080` to trigger a review manually, or wait for
   the next poll cycle for newly-opened PRs to be reviewed automatically.

## Claude/Codex/Kiro fallback

The review runner (`src/review_runner.py`) always tries headless Claude
first. If Claude reports a usage-limit failure (exhausted quota, rate
limit, or session limit — whether via a non-zero exit or an exit-0 result
that mentions the limit), the runner retries the same PR with device-authenticated
Codex. If Codex also reports a quota failure, it uses headless Kiro CLI with the
`pr-reviewer` agent config in `.kiro/agents/pr-reviewer.json`. Any other failure
propagates immediately without falling back.

## Provider priority

Use **Review providers** in the web dashboard to set the review priority for
new work. Drag a provider or use its arrow controls, then save. The selected
order is stored in the persistent SQLite volume and applies to both scheduled
batch reviews and reviews submitted from the dashboard. A provider is only
skipped when it reports a usage or quota limit; other failures are returned
immediately.

## Design

See `docs/superpowers/specs/2026-09-04-bitbucket-pr-review-bot-design.md`.
