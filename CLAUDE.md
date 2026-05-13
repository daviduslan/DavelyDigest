# Davely Digest — Project Notes for Claude

## What this project is

A Python script (`digest.py`) that fetches RSS/Substack feeds, scores articles for
relevance via the Claude API, and sends a daily HTML email digest. Runs on a local Mac
via launchd — GitHub Actions schedule is disabled.

## Running locally

```bash
./run_local.sh          # runs digest.py with env from .env
.venv/bin/python -m pytest tests/ -v   # run tests
```

`run_local.sh` explicitly unsets `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` before
sourcing `.env` to prevent ambient shell vars (e.g. a work LiteLLM proxy) from
intercepting the request.

## Key files

| File | Purpose |
|---|---|
| `digest.py` | Main script — all logic lives here |
| `context.local.txt` | Personalization context passed to Claude for scoring — **gitignored, never commit** |
| `.env` | All secrets/credentials — **gitignored, never commit** |
| `run_local.sh` | Local runner — sources `.env`, selects venv python |
| `com.davelydigest.plist` | launchd agent — install to `~/Library/LaunchAgents/` |
| `tests/test_digest.py` | 29 unit tests (no network calls — all mocked) |

## Architecture

`digest.py` has four main functions:

- **`fetch_recent_items(feeds, lookback_hours)`** → `(items, warnings)`
  Fetches all feeds. Substack URLs are detected and routed to `_fetch_substack()` which
  calls the Substack JSON API (`/api/v1/posts`) instead of RSS — Substack RSS is blocked
  by Cloudflare on cloud runner IPs. Non-Substack feeds use `requests.get` +
  `feedparser.parse(resp.content)` with browser-spoofing headers. Returns a warnings list
  for empty, broken, or stale (60+ day) feeds.

- **`score_and_annotate(items, client)`** → `None`
  Sends all items to Claude in one API call. Mutates items in-place, adding `score` and
  `editorial_note`. Strips markdown fences before JSON parsing. Falls back to score 5 / 
  empty note on parse failure.

- **`render_email(items, warnings)`** → `(subject, html_body)`
  Builds the HTML email. Uses `html.escape()` on all feed-derived content (title,
  editorial note, warning reasons, source names). Validates URLs and replaces
  `javascript:` hrefs with `#`. Table-based two-column layout for email client compat.

- **`send_email(subject, body)`**
  Standard SMTP with `ssl.create_default_context()`.

## Feed configuration

Each feed in `FEEDS` has: `url`, `domain`, `source`, `vendor` (bool).

`vendor: True` feeds get a badge in the email. Valid domains:
`Data Engineering`, `Data Governance`, `Analytics & BI`, `Data Science & ML`,
`AI & Data`, `Data Leadership`.

Several feeds are Substack — they work locally but were blocked from GitHub Actions
(Azure IPs blocked at Cloudflare level). This is why the schedule moved to launchd.

## Personalization context

`context.local.txt` (gitignored) is read at startup into `LEARNING_PLAN_CONTEXT` and
injected into the Claude scoring prompt. If the file is missing, scoring still works —
just without personalized context. Never hardcode personal context in `digest.py`.

## Testing notes

- All tests mock network calls — `requests.get` and `feedparser.parse`
- Stacked `@patch` decorators: bottom decorator = first argument after `self`
- Run the full suite after any change to `digest.py`

## Key constants

- `LOOKBACK_HOURS = 24` — lookback window for feed items
- `MIN_SCORE = 6` — minimum relevance score to include in digest
- `MODEL = "claude-sonnet-4-5"` — Claude model used for scoring
