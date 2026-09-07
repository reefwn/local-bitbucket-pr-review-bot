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
4. One-time Kiro CLI login (used as the fallback agent when Claude hits its
   usage limit):
   ```
   docker compose run --rm --entrypoint kiro-cli bot login --use-device-flow
   ```
   `--use-device-flow` is required since the container can't handle the
   browser loopback redirect — this prints a code and URL to enter on
   another device instead.
   Follow the device/browser auth flow. Credentials persist on the
   `kiro-auth` and `kiro-aws-sso` volumes, so this is only needed once.
5. Start everything: `docker compose up -d`
6. Open `http://localhost:8080` to trigger a review manually, or wait for
   the next poll cycle for newly-opened PRs to be reviewed automatically.

## Claude/Kiro fallback

The review runner (`src/review_runner.py`) always tries headless Claude
first. If Claude reports a usage-limit failure (exhausted quota, rate
limit, or session limit — whether via a non-zero exit or an exit-0 result
that mentions the limit), the runner automatically retries the same PR
review with headless Kiro CLI using the `pr-reviewer` agent config in
`.kiro/agents/pr-reviewer.json`. Any other Claude failure propagates
immediately without falling back. See `CLAUDE.md` / `KIRO.md` for details.

## Design

See `docs/superpowers/specs/2026-09-04-bitbucket-pr-review-bot-design.md`.
