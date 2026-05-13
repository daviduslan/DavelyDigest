# Davely Digest

A daily email digest that crawls specified sources of information, scores each item for
relevance to a personalized learning plan using Claude, and sends a formatted HTML email
with editorial notes explaining why each article matters.

## What it does

1. Fetches the last 24 hours of posts from ~18 curated sources across five domains
2. Sends all items to Claude API for relevance scoring (1–10) and editorial annotation
3. Filters to items scoring ≥ 6, groups by domain, sorts by score
4. Sends a formatted HTML digest email with feed health warnings for any stale or broken sources

Runs automatically via **launchd** on your local Mac (Mon–Fri at 7 AM local time).

---

## Setup

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd DavelyDigest
```

### 2. Install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Create your `.env` file

Copy the example and fill in your values:

```bash
cp .env.example .env
```

Open `.env` and set:

```
ANTHROPIC_API_KEY=sk-ant-...
DIGEST_RECIPIENT_EMAIL=you@example.com
DIGEST_SENDER_EMAIL=you@gmail.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=you@gmail.com
SMTP_PASSWORD=your-app-password
```

**Gmail App Password setup:**
1. Google Account → Security → 2-Step Verification (must be enabled)
2. Search for **App passwords**, create one named "Davely Digest"
3. Use the 16-character password as `SMTP_PASSWORD`

> All values in `.env` must be quoted if they contain spaces or special characters.
> `.env` is gitignored and will never be committed.

### 4. Create your learning plan context

Create a file called `context.local.txt` in the project root. This is what Claude uses
to evaluate article relevance — write it to describe your role, goals, and focus areas.
It is gitignored and stays private.

Example:
```
I'm a data leader focused on building and scaling a modern data platform.
My priorities are data engineering (dbt, Airflow, lakehouse architecture),
data governance, and helping non-technical stakeholders make data-driven decisions.
Prefer strategic and leadership-oriented content over deep technical tutorials.
```

If `context.local.txt` is missing, the digest still runs — Claude just scores without
personalized context.

### 5. Test the digest manually

```bash
./run_local.sh
```

You should receive an email within ~2 minutes.

### 6. Schedule it with launchd (Mac only)

Install the included launchd agent to run the digest automatically Mon–Fri at 7 AM:

```bash
cp com.davelydigest.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.davelydigest.plist
```

> If your Mac is asleep at 7 AM, the job runs as soon as it wakes up.

**Useful commands:**

```bash
# Trigger a manual run
launchctl start com.davelydigest

# Check last exit code
launchctl list | grep davelydigest

# View logs
tail -f ~/Library/Logs/davelydigest.log
tail -f ~/Library/Logs/davelydigest.error.log

# Disable the schedule
launchctl unload ~/Library/LaunchAgents/com.davelydigest.plist
```

---

## Customization

### Adding or removing sources

Edit the `FEEDS` list in `digest.py`. Each entry needs:

```python
{"url": "https://example.com/feed.xml", "domain": "Data Engineering", "source": "Example Blog", "vendor": False}
```

Set `"vendor": True` for content published by vendors/companies selling a product — these
get a badge in the email so you can calibrate accordingly.

Valid domain values (used for grouping and color coding):

| Domain | Color |
|---|---|
| `"Data Engineering"` | Blue |
| `"Data Governance"` | Purple |
| `"Analytics & BI"` | Teal |
| `"Data Science & ML"` | Orange |
| `"AI & Data"` | Red |
| `"Data Leadership"` | Green |

**Substack feeds** are fetched via the Substack JSON API (`/api/v1/posts`) rather than RSS,
which improves reliability.

### Adjusting the relevance threshold

Change `MIN_SCORE` in `digest.py` (default: `6`).
Raise to `7`–`8` for a tighter digest. Lower to `5` for more volume.

### Updating your learning plan context

Edit `context.local.txt` any time your focus areas evolve. No code change needed.

---

## Feed health

The digest automatically warns you when a feed:
- Returns no entries
- Has a parse error
- Has not published anything in 60+ days

Warnings appear at the bottom of each email so you can decide whether to replace stale sources.

---

## Cost estimate

Each run makes one Claude API call covering all articles fetched that day.
Typical cost: **$0.01–0.03 per run** (~$0.25–0.65/month for weekday-only runs).
