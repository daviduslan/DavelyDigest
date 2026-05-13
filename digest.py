#!/usr/bin/env python3
"""
Davely Digest
Fetches RSS feeds, scores relevance via Claude API, and sends a daily email digest.
"""

import os
import json
import smtplib
import feedparser
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from anthropic import Anthropic

# ── Configuration ─────────────────────────────────────────────────────────────

RECIPIENT_EMAIL = os.environ["DIGEST_RECIPIENT_EMAIL"]
SENDER_EMAIL    = os.environ["DIGEST_SENDER_EMAIL"]
SMTP_HOST       = os.environ["SMTP_HOST"]           # e.g. smtp.gmail.com
SMTP_PORT       = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER       = os.environ["SMTP_USER"]
SMTP_PASSWORD   = os.environ["SMTP_PASSWORD"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# How many hours back to look for new items (24 = daily)
LOOKBACK_HOURS = 24

# Minimum relevance score (1-10) to include in digest
MIN_SCORE = 6

# ── RSS Feed Sources ───────────────────────────────────────────────────────────

FEEDS = [
    # Data Engineering
    {"url": "https://www.databricks.com/feed",                          "domain": "Data Engineering",   "source": "Databricks Blog",             "vendor": True},
    {"url": "https://seattledataguy.substack.com/feed",                 "domain": "Data Engineering",   "source": "Seattle Data Guy",            "vendor": False},
    {"url": "https://www.dataengineeringweekly.com/feed",               "domain": "Data Engineering",   "source": "Data Engineering Weekly",     "vendor": False},
    {"url": "https://airbyte.com/blog/rss.xml",                         "domain": "Data Engineering",   "source": "Airbyte Blog",                "vendor": True},
    {"url": "https://www.getdbt.com/blog/rss.xml",                      "domain": "Data Engineering",   "source": "dbt Blog",                    "vendor": False},

    # Data Governance
    {"url": "https://tdwi.org/rss-feeds/all-articles.aspx",             "domain": "Data Governance",    "source": "TDWI",                        "vendor": False},
    {"url": "https://atlan.com/blog/rss/",                              "domain": "Data Governance",    "source": "Atlan Blog",                  "vendor": True},

    # Analytics & BI
    {"url": "https://locallyoptimistic.com/feed/",                      "domain": "Analytics & BI",     "source": "Locally Optimistic",          "vendor": False},
    {"url": "https://medium.com/feed/towards-data-science",             "domain": "Analytics & BI",     "source": "Towards Data Science",        "vendor": False},

    # Data Science & ML
    {"url": "https://eugeneyan.com/rss/",                               "domain": "Data Science & ML",  "source": "Eugene Yan",                  "vendor": False},
    {"url": "https://huyenchip.com/feed.xml",                           "domain": "Data Science & ML",  "source": "Chip Huyen",                  "vendor": False},

    # Data Leadership & Strategy
    {"url": "https://benn.substack.com/feed",                           "domain": "Data Leadership",    "source": "Benn Stancil",                "vendor": False},
    {"url": "https://www.oreilly.com/radar/topics/data/feed/index.xml", "domain": "Data Leadership",    "source": "O'Reilly Radar",              "vendor": False},
    {"url": "https://hdsr.mitpress.mit.edu/rss/feed.xml",               "domain": "Data Leadership",    "source": "Harvard Data Science Review", "vendor": False},
]

# ── Learning plan context (used in the Claude prompt) ─────────────────────────

LEARNING_PLAN_CONTEXT = """
The reader is transitioning from Senior People Operations Manager into a Global Head of Data role
at a software company. Their development plan covers five domains:

1. Data Engineering — pipelines, ELT/ETL, dbt, orchestration (Airflow/Dagster/Prefect),
   lakehouse architecture, data contracts, CDC, the modern data stack
2. Data Governance — data catalogs, lineage, quality frameworks, observability, RBAC,
   PII classification, data mesh vs centralized models, governance as org accountability
3. Analytics & BI — semantic layers, metrics stores, headless BI, self-serve analytics,
   operational analytics, product analytics tooling
4. Data Science & ML — MLOps, feature stores, model deployment/drift, experimentation,
   A/B testing, causal inference; goal is leadership fluency not practitioner depth
5. Data Leadership & Strategy — data team org design, build vs buy decisions, data ROI
   framing, executive communication, centralized vs embedded team models

The reader has a strong analytics background and product management experience.
They are vocabulary-building and developing strategic fluency, not learning to code from scratch.
Content that helps a data leader understand the landscape, make decisions, and lead teams
is more valuable than deep technical tutorials.
"""

# ── Feed fetching ──────────────────────────────────────────────────────────────

def fetch_recent_items(feeds: list[dict], lookback_hours: int) -> list[dict]:
    """Fetch RSS feed items published within the lookback window."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    items = []

    for feed_config in feeds:
        try:
            parsed = feedparser.parse(feed_config["url"])
            for entry in parsed.entries:
                # Parse published date — feedparser normalizes to time.struct_time
                published = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
                    published = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)

                # Include if within window (or if date unavailable, include anyway)
                if published is None or published >= cutoff:
                    summary = ""
                    if hasattr(entry, "summary"):
                        # Strip basic HTML tags for cleaner text
                        import re
                        summary = re.sub(r"<[^>]+>", "", entry.summary)[:500]

                    items.append({
                        "title":   entry.get("title", "No title"),
                        "url":     entry.get("link", ""),
                        "summary": summary,
                        "source":  feed_config["source"],
                        "domain":  feed_config["domain"],
                        "published": published.strftime("%b %d") if published else "Recent",
                        "vendor":  feed_config.get("vendor", False),
                    })
        except Exception as e:
            print(f"⚠️  Failed to fetch {feed_config['source']}: {e}")

    return items

# ── AI relevance scoring ───────────────────────────────────────────────────────

def score_and_annotate(items: list[dict], client: Anthropic) -> list[dict]:
    """
    Send all items to Claude in a single batch call.
    Returns items with added 'score' (1-10) and 'editorial_note' fields.
    """
    if not items:
        return []

    items_payload = json.dumps([
        {"id": i, "title": item["title"], "summary": item["summary"], "domain": item["domain"]}
        for i, item in enumerate(items)
    ], indent=2)

    prompt = f"""You are helping curate a daily data leadership digest for someone with the following learning plan:

{LEARNING_PLAN_CONTEXT}

Below is a list of articles fetched from curated data industry sources today.
For each article, evaluate its relevance to this person's learning plan and growth toward becoming a Global Head of Data.

Return a JSON array — one object per article — with these fields:
- id: (same integer as input)
- score: integer 1-10 (10 = highly relevant, 1 = not relevant)
- editorial_note: 1-2 sentence explanation of WHY this is or isn't worth reading for this person's specific journey. Be specific — reference their learning plan domains. If score < 6, still provide a brief note.

Scoring guidance:
- 8-10: Directly addresses a gap domain (data engineering, governance, DS/ML leadership) or data leadership strategy
- 6-7: Useful context, adjacent relevance, or good vocabulary-building
- 4-5: Tangentially related, mostly technical depth beyond leadership need
- 1-3: Not relevant to this person's goals

Articles to evaluate:
{items_payload}

Respond with ONLY the JSON array, no preamble or markdown fences."""

    response = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}]
    )

    try:
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```", 2)[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.rsplit("```", 1)[0].strip()
        scored = json.loads(raw)
        score_map = {s["id"]: s for s in scored}
        for i, item in enumerate(items):
            if i in score_map:
                item["score"] = score_map[i].get("score", 0)
                item["editorial_note"] = score_map[i].get("editorial_note", "")
            else:
                item["score"] = 0
                item["editorial_note"] = ""
    except json.JSONDecodeError as e:
        print(f"⚠️  Failed to parse Claude response: {e}")
        print(f"   Raw response: {response.content[0].text[:500]}")
        for item in items:
            item["score"] = 5
            item["editorial_note"] = ""

    return items

# ── Email rendering ────────────────────────────────────────────────────────────

DOMAIN_COLORS = {
    "Data Engineering":   "#00875a",
    "Data Governance":    "#0052cc",
    "Analytics & BI":     "#6554c0",
    "Data Science & ML":  "#bf2600",
    "Data Leadership":    "#ff7452",
}

def render_email(items: list[dict]) -> tuple[str, str]:
    """Returns (subject, html_body) for the digest email."""
    today = datetime.now().strftime("%A, %B %d")
    count = len(items)

    # Group by domain
    by_domain: dict[str, list] = {}
    for item in items:
        by_domain.setdefault(item["domain"], []).append(item)

    # Sort domains by avg score desc
    domain_order = sorted(by_domain.keys(), key=lambda d: sum(i["score"] for i in by_domain[d]) / len(by_domain[d]), reverse=True)

    sections_html = ""
    for domain in domain_order:
        domain_items = sorted(by_domain[domain], key=lambda x: x["score"], reverse=True)
        color = DOMAIN_COLORS.get(domain, "#172b4d")
        items_html = ""
        for item in domain_items:
            score_color = "#00875a" if item["score"] >= 8 else "#ff7452" if item["score"] >= 6 else "#6b778c"
            items_html += f"""
            <tr>
              <td style="padding:14px 0;border-bottom:1px solid #f0f0f0;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
                  <div style="flex:1;">
                    <a href="{item['url']}" style="font-size:14px;font-weight:600;color:#172b4d;text-decoration:none;line-height:1.4;">{item['title']}</a>
                    <div style="margin-top:4px;font-size:11px;color:#6b778c;">{item['source']} · {item['published']}{' · <span style="color:#ff7452;font-weight:600;">vendor source</span>' if item.get('vendor') else ''}</div>
                    {f'<div style="margin-top:8px;font-size:13px;color:#42526e;line-height:1.5;background:#f8f9fa;border-left:3px solid {color};padding:6px 10px;border-radius:0 3px 3px 0;">{item["editorial_note"]}</div>' if item.get("editorial_note") else ""}
                  </div>
                  <div style="flex-shrink:0;min-width:28px;height:28px;padding:0 6px;border-radius:14px;background:{score_color};color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;text-align:center;line-height:28px;">{item['score']}</div>
                </div>
              </td>
            </tr>"""

        sections_html += f"""
        <tr>
          <td style="padding:24px 0 0;">
            <div style="display:inline-block;background:{color};color:#fff;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;padding:4px 10px;border-radius:3px;margin-bottom:4px;">{domain}</div>
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              {items_html}
            </table>
          </td>
        </tr>"""

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0"></head>
<body style="margin:0;padding:0;background:#f4f5f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f4f5f7;padding:32px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" border="0" style="max-width:600px;background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.1);">

        <!-- Header -->
        <tr>
          <td style="background:#172b4d;padding:24px 32px;">
            <div style="font-size:11px;color:#7a8fa6;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Davely Digest</div>
            <div style="font-size:22px;font-weight:700;color:#ffffff;">{today}</div>
            <div style="font-size:13px;color:#a0b0c4;margin-top:4px;">{count} items across {len(by_domain)} domains · scored by relevance to your learning plan</div>
          </td>
        </tr>

        <!-- Body -->
        <tr>
          <td style="padding:8px 32px 32px;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              {sections_html}
            </table>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="background:#f4f5f7;padding:16px 32px;border-top:1px solid #e8e8e8;">
            <div style="font-size:11px;color:#6b778c;text-align:center;">
              Scores reflect relevance to your data leadership learning plan (1–10).<br/>
              Sources: Databricks†, Seattle Data Guy, DE Weekly, Airbyte†, dbt Blog, Atlan†, TDWI, Locally Optimistic, Towards Data Science, Eugene Yan, Chip Huyen, Benn Stancil, O'Reilly Radar, Harvard DSR. &nbsp;†vendor source
            </div>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    subject = f"📊 Davely Digest — {today} ({count} items)"
    return subject, html

# ── Email sending ──────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

    print(f"✅ Digest sent to {RECIPIENT_EMAIL}")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print(f"🔍 Fetching feeds (last {LOOKBACK_HOURS}h)...")
    items = fetch_recent_items(FEEDS, LOOKBACK_HOURS)
    print(f"   Found {len(items)} raw items")

    if not items:
        print("   No new items found — skipping email.")
        return

    print("🤖 Scoring and annotating with Claude...")
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    items = score_and_annotate(items, client)

    # Filter to relevant items
    relevant = [i for i in items if i.get("score", 0) >= MIN_SCORE]
    relevant.sort(key=lambda x: x["score"], reverse=True)
    print(f"   {len(relevant)} items passed relevance threshold (score ≥ {MIN_SCORE})")

    if not relevant:
        print("   Nothing relevant today — skipping email.")
        return

    print("📧 Sending digest email...")
    subject, html = render_email(relevant)
    send_email(subject, html)

if __name__ == "__main__":
    main()
