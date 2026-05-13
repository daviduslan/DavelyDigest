# Data Leadership Digest

A daily email digest that crawls curated data industry RSS feeds, scores each item for
relevance to a data leadership learning plan using Claude, and emails a formatted digest
with editorial notes explaining why each article matters.

## What it does

1. Fetches the last 24 hours of posts from ~15 curated sources across five domains
2. Sends all items to Claude API for relevance scoring (1–10) and editorial annotation
3. Filters to items scoring ≥ 6, groups by domain, sorts by score
4. Sends a formatted HTML digest email

Runs automatically via GitHub Actions on a schedule (default: 7 AM Pacific, weekdays).

---

## Setup

### 1. Create a GitHub repository

Create a new **private** repo (recommended — keeps your secrets context private).
Push all files from this project into it, preserving the directory structure:

```
your-repo/
├── digest.py
├── requirements.txt
└── .github/
    └── workflows/
        └── digest.yml
```

### 2. Set up email sending

The script uses standard SMTP. The easiest option is a **Gmail App Password**:

1. Go to your Google Account → Security → 2-Step Verification (must be enabled)
2. Under "2-Step Verification", scroll to **App passwords**
3. Create a new app password — name it "Data Digest"
4. Copy the 16-character password

Your SMTP settings will be:
- `SMTP_HOST`: `smtp.gmail.com`
- `SMTP_PORT`: `587`
- `SMTP_USER`: your Gmail address (e.g. `you@gmail.com`)
- `SMTP_PASSWORD`: the 16-character app password
- `DIGEST_SENDER_EMAIL`: your Gmail address
- `DIGEST_RECIPIENT_EMAIL`: wherever you want to receive it (can be same address)

### 3. Add GitHub Actions secrets

In your GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**

Add each of these:

| Secret name               | Value                        |
|---------------------------|------------------------------|
| `ANTHROPIC_API_KEY`       | Your Anthropic API key       |
| `DIGEST_RECIPIENT_EMAIL`  | Your email address           |
| `DIGEST_SENDER_EMAIL`     | Your Gmail address           |
| `SMTP_HOST`               | `smtp.gmail.com`             |
| `SMTP_PORT`               | `587`                        |
| `SMTP_USER`               | Your Gmail address           |
| `SMTP_PASSWORD`           | Your Gmail app password      |

### 4. Test it manually

Once secrets are set:
1. Go to your repo → **Actions** tab
2. Click **Daily Data Leadership Digest** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Watch the run logs — you should receive an email within ~2 minutes

### 5. Adjust the schedule

The default schedule in `digest.yml` is 7:00 AM Pacific, weekdays only.

To change the time, edit the cron expression:
```yaml
- cron: "0 15 * * 1-5"
#         │  │  │ │ └── Days of week (1-5 = Mon-Fri; * = every day)
#         │  └──┘ └──── Month / Day of month (* = every)
#         └──────────── Hour in UTC (15 UTC = 7 AM PDT / 8 AM PST)
```

Common alternatives:
- `"0 14 * * 1-5"` → 6 AM Pacific (PDT)
- `"0 15 * * *"`   → 7 AM Pacific every day including weekends

---

## Customization

### Adding or removing sources

Edit the `FEEDS` list in `digest.py`. Each entry needs:
```python
{"url": "https://example.com/feed.xml", "domain": "Data Engineering", "source": "Example Blog"}
```

Valid domain values (used for grouping and color coding):
- `"Data Engineering"`
- `"Data Governance"`
- `"Analytics & BI"`
- `"Data Science & ML"`
- `"Data Leadership"`

### Adjusting the relevance threshold

Change `MIN_SCORE` in `digest.py` (default: `6`).
Raise to `7` or `8` for a tighter, shorter digest. Lower to `5` for more volume.

### Updating your learning plan context

The `LEARNING_PLAN_CONTEXT` string in `digest.py` is what Claude uses to evaluate relevance.
As your focus areas evolve, update this string to reflect where you are in your development plan.

---

## Cost estimate

Each run makes one Claude API call with ~15–40 articles.
Typical cost: **$0.01–0.03 per day** (~$0.30–0.90/month).
GitHub Actions: free tier includes 2,000 minutes/month — this job uses ~1–2 minutes per run.
