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
