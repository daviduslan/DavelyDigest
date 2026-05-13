#!/usr/bin/env python3
"""
Davely Digest
Fetches RSS feeds, scores relevance via Claude API, and sends a daily email digest.
"""

import os
import re
import ssl
import html
import json
import smtplib
import feedparser
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from anthropic import Anthropic

# ── Configuration ─────────────────────────────────────────────────────────────

RECIPIENT_EMAIL   = os.environ.get("DIGEST_RECIPIENT_EMAIL", "")
SENDER_EMAIL      = os.environ.get("DIGEST_SENDER_EMAIL", "")
SMTP_HOST         = os.environ.get("SMTP_HOST", "")
SMTP_PORT         = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER         = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD     = os.environ.get("SMTP_PASSWORD", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

MODEL = "claude-sonnet-4-5"

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

    # AI & Data Leadership Intersection
    {"url": "https://thesequence.substack.com/feed",                    "domain": "AI & Data",          "source": "The Sequence",                "vendor": False},
    {"url": "https://importai.substack.com/feed",                       "domain": "AI & Data",          "source": "Import AI",                   "vendor": False},
    {"url": "https://magazine.sebastianraschka.com/feed",               "domain": "AI & Data",          "source": "Ahead of AI",                 "vendor": False},
    {"url": "https://gradientflow.com/blog/feed/",                      "domain": "AI & Data",          "source": "Gradient Flow",               "vendor": False},

    # Data Leadership & Strategy
    {"url": "https://benn.substack.com/feed",                           "domain": "Data Leadership",    "source": "Benn Stancil",                "vendor": False},
    {"url": "https://www.oreilly.com/radar/topics/data/feed/index.xml", "domain": "Data Leadership",    "source": "O'Reilly Radar",              "vendor": False},
    {"url": "https://hdsr.mitpress.mit.edu/rss/feed.xml",               "domain": "Data Leadership",    "source": "Harvard Data Science Review", "vendor": False},
]

# ── Learning plan context (used in the Claude prompt) ─────────────────────────

LEARNING_PLAN_CONTEXT = """
The reader is transitioning from Senior People Operations Manager into a Global Head of Data role
at a software company. Their development plan covers six domains:

1. Data Engineering — pipelines, ELT/ETL, dbt, orchestration (Airflow/Dagster/Prefect),
   lakehouse architecture, data contracts, CDC, and the modern data stack as a whole.

2. Data Governance — data catalogs, lineage, quality frameworks, observability, RBAC and ABAC,
   PII classification, data mesh vs centralized models, and governance as an org accountability
   structure. Increasingly includes AI governance: model cards, lineage for ML pipelines,
   bias monitoring, and responsible AI frameworks.

3. Analytics & BI — semantic layers, metrics stores, headless BI, self-serve analytics,
   operational analytics, and product analytics tooling. Calibration more than new learning.

4. Data Science & ML — MLOps lifecycle, feature stores, model deployment and drift monitoring,
   experimentation and A/B testing rigor, causal inference. Goal is leadership fluency —
   knowing when results are statistically meaningful, evaluating whether experiments are
   designed correctly, and having enough intuition to ask good questions of DS/ML teams.

5. AI & Data — how AI is reshaping the data platform landscape and the data leader role.
   Covers: AI-native data tooling, LLMs applied to data workflows (text-to-SQL, data agents,
   AI-assisted governance), the organizational implications of embedding AI into data products,
   and what AI governance means for a data team specifically. This is distinct from ML
   engineering depth — the focus is strategic and leadership-oriented.

6. Data Leadership & Strategy — data team org design (centralized vs embedded vs hybrid),
   build vs buy decisions for data tooling, data team roadmapping, making the case for data
   investment at the executive level, operating as a peer to Heads of Engineering and Product,
   and defining what the data function uniquely contributes to a software org.

The reader has a strong analytics background and product management experience.
They are vocabulary-building and developing strategic fluency — not learning to code from scratch.
Content that helps a data leader understand the landscape, make decisions, and lead teams
is more valuable than deep technical tutorials.
Prioritize content that connects AI developments back to data team strategy, tooling decisions,
or governance responsibilities over pure AI research content.
"""

# ── Feed fetching ──────────────────────────────────────────────────────────────

def _parse_date(entry) -> datetime | None:
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    if hasattr(entry, "updated_parsed") and entry.updated_parsed:
        return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
    return None

def fetch_recent_items(feeds: list[dict], lookback_hours: int) -> tuple[list[dict], list[dict]]:
    """Fetch RSS feed items published within the lookback window.
    Returns (items, feed_warnings) where feed_warnings is a list of dicts
    with 'source' and 'reason' keys."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    stale_cutoff = datetime.now(timezone.utc) - timedelta(days=60)
    items = []
    feed_warnings = []

    for feed_config in feeds:
        try:
            parsed = feedparser.parse(feed_config["url"])

            # Check for fetch/parse failure
            if parsed.bozo and not parsed.entries:
                feed_warnings.append({
                    "source": feed_config["source"],
                    "reason": f"Failed to fetch or parse feed ({type(parsed.bozo_exception).__name__ if parsed.bozo_exception else 'unknown error'})"
                })
                continue

            if not parsed.entries:
                feed_warnings.append({
                    "source": feed_config["source"],
                    "reason": "Feed returned no entries"
                })
                continue

            # Check for stale feed — look at most recent entry date
            most_recent = None
            for entry in parsed.entries:
                pub = _parse_date(entry)
                if pub and (most_recent is None or pub > most_recent):
                    most_recent = pub

            if most_recent and most_recent < stale_cutoff:
                days_ago = (datetime.now(timezone.utc) - most_recent).days
                feed_warnings.append({
                    "source": feed_config["source"],
                    "reason": f"Last post was {days_ago} days ago"
                })

            # Normal item collection
            for entry in parsed.entries:
                published = _parse_date(entry)

                if published is None or published >= cutoff:
                    summary = ""
                    if hasattr(entry, "summary"):
                        summary = re.sub(r"<[^>]+>", "", entry.summary)[:500]

                    items.append({
                        "title":     entry.get("title", "No title"),
                        "url":       entry.get("link", ""),
                        "summary":   summary,
                        "source":    feed_config["source"],
                        "domain":    feed_config["domain"],
                        "vendor":    feed_config.get("vendor", False),
                        "published": published.strftime("%b %d") if published else "Recent",
                    })

        except Exception as e:
            feed_warnings.append({
                "source": feed_config["source"],
                "reason": f"Exception during fetch: {e}"
            })

    return items, feed_warnings

# ── AI relevance scoring ───────────────────────────────────────────────────────

def score_and_annotate(items: list[dict], client: Anthropic) -> None:
    """Mutates items in place, adding 'score' (1-10) and 'editorial_note' fields."""
    if not items:
        return

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
        model=MODEL,
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

# ── Email rendering ────────────────────────────────────────────────────────────

DOMAIN_COLORS = {
    "Data Engineering":   "#00875a",
    "Data Governance":    "#0052cc",
    "Analytics & BI":     "#6554c0",
    "Data Science & ML":  "#bf2600",
    "AI & Data":          "#0098a1",
    "Data Leadership":    "#ff7452",
}

def render_email(items: list[dict], feed_warnings: list[dict]) -> tuple[str, str]:
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
            safe_url = item["url"] if re.match(r"^https?://", item.get("url", "")) else "#"
            href = html.escape(safe_url)
            title = html.escape(item["title"])
            source = html.escape(item["source"])
            published = html.escape(item["published"])
            note = html.escape(item.get("editorial_note", ""))
            vendor_badge = ' · <span style="color:#ff7452;font-weight:600;">vendor source</span>' if item.get("vendor") else ""
            note_html = f'<div style="margin-top:8px;font-size:13px;color:#42526e;line-height:1.5;background:#f8f9fa;border-left:3px solid {color};padding:6px 10px;border-radius:0 3px 3px 0;">{note}</div>' if note else ""
            items_html += f"""
            <tr>
              <td style="padding:14px 0;border-bottom:1px solid #f0f0f0;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px;">
                  <div style="flex:1;">
                    <a href="{href}" style="font-size:14px;font-weight:600;color:#172b4d;text-decoration:none;line-height:1.4;">{title}</a>
                    <div style="margin-top:4px;font-size:11px;color:#6b778c;">{source} · {published}{vendor_badge}</div>
                    {note_html}
                  </div>
                  <div style="flex-shrink:0;min-width:28px;height:28px;padding:0 6px;border-radius:14px;background:{score_color};color:#fff;font-size:12px;font-weight:700;display:flex;align-items:center;justify-content:center;text-align:center;line-height:28px;">{item['score']}</div>
                </div>
              </td>
            </tr>"""

        sections_html += f"""
        <tr>
          <td style="padding:24px 0 0;">
            <div style="display:inline-block;background:{color};color:#fff;font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;padding:4px 10px;border-radius:3px;margin-bottom:4px;">{html.escape(domain)}</div>
            <table width="100%" cellpadding="0" cellspacing="0" border="0">
              {items_html}
            </table>
          </td>
        </tr>"""

    warnings_html = ""
    if feed_warnings:
        warning_rows = "".join(
            f'<tr><td style="padding:3px 0;font-size:11px;color:#6b778c;">⚠️ <strong>{html.escape(w["source"])}</strong> — {html.escape(w["reason"])}</td></tr>'
            for w in feed_warnings
        )
        warnings_html = f"""
        <tr>
          <td style="padding:16px 32px 0;">
            <div style="background:#fffbe6;border:1px solid #ffe58f;border-radius:4px;padding:12px 16px;">
              <div style="font-size:11px;font-weight:700;color:#ad6800;margin-bottom:6px;text-transform:uppercase;letter-spacing:0.5px;">Feed Health Warnings</div>
              <table cellpadding="0" cellspacing="0" border="0">{warning_rows}</table>
            </div>
          </td>
        </tr>"""

    html_body = f"""<!DOCTYPE html>
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

{warnings_html}
        <!-- Footer -->
        <tr>
          <td style="background:#f4f5f7;padding:16px 32px;border-top:1px solid #e8e8e8;">
            <div style="font-size:11px;color:#6b778c;text-align:center;">
              Scores reflect relevance to your data leadership learning plan (1–10).<br/>
              Sources: Databricks†, Seattle Data Guy, DE Weekly, Airbyte†, dbt Blog, Atlan†, TDWI, Locally Optimistic, Towards Data Science, Eugene Yan, Chip Huyen, The Sequence, Import AI, Ahead of AI, Gradient Flow, Benn Stancil, O'Reilly Radar, Harvard DSR. &nbsp;†vendor source
            </div>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>"""

    subject = f"📊 Davely Digest — {today} ({count} items)"
    return subject, html_body

# ── Email sending ──────────────────────────────────────────────────────────────

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())

    print(f"✅ Digest sent to {RECIPIENT_EMAIL}")

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    missing = [k for k, v in {
        "DIGEST_RECIPIENT_EMAIL": RECIPIENT_EMAIL,
        "DIGEST_SENDER_EMAIL": SENDER_EMAIL,
        "SMTP_HOST": SMTP_HOST,
        "SMTP_USER": SMTP_USER,
        "SMTP_PASSWORD": SMTP_PASSWORD,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }.items() if not v]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    print(f"🔍 Fetching feeds (last {LOOKBACK_HOURS}h)...")
    items, feed_warnings = fetch_recent_items(FEEDS, LOOKBACK_HOURS)
    print(f"   Found {len(items)} raw items")
    if feed_warnings:
        print(f"   ⚠️  {len(feed_warnings)} feed warning(s):")
        for w in feed_warnings:
            print(f"      {w['source']}: {w['reason']}")

    if not items:
        print("   No new items found — skipping email.")
        return

    print("🤖 Scoring and annotating with Claude...")
    client = Anthropic(api_key=ANTHROPIC_API_KEY)
    score_and_annotate(items, client)

    # Filter to relevant items
    relevant = [i for i in items if i.get("score", 0) >= MIN_SCORE]
    relevant.sort(key=lambda x: x["score"], reverse=True)
    print(f"   {len(relevant)} items passed relevance threshold (score ≥ {MIN_SCORE})")

    if not relevant:
        print("   Nothing relevant today — skipping email.")
        return

    print("📧 Sending digest email...")
    subject, html = render_email(relevant, feed_warnings)
    send_email(subject, html)

if __name__ == "__main__":
    main()
