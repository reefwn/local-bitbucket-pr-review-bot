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
