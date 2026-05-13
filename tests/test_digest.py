import json
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import digest


class MockEntry:
    """Minimal feedparser entry stand-in."""
    def __init__(self, title="Title", link="https://example.com", summary="Summary",
                 published_parsed=None, updated_parsed=None):
        self.title = title
        self.link = link
        self.summary = summary
        self.published_parsed = published_parsed
        self.updated_parsed = updated_parsed

    def get(self, key, default=""):
        return getattr(self, key, default)


def _struct(dt: datetime):
    """Convert datetime to a feedparser-style time.struct_time tuple."""
    return dt.timetuple()[:6] + (0, 0, 0)


def _mock_parsed(entries=None, bozo=False, bozo_exception=None):
    p = MagicMock()
    p.entries = entries or []
    p.bozo = bozo
    p.bozo_exception = bozo_exception
    return p


FEED = {"url": "https://example.com/feed", "source": "Test Source", "domain": "Data Engineering", "vendor": False}


# ── _parse_date ────────────────────────────────────────────────────────────────

class TestParseDate(unittest.TestCase):
    def test_uses_published_parsed(self):
        entry = MockEntry(published_parsed=_struct(datetime(2025, 5, 1, 12, 0, 0, tzinfo=timezone.utc)))
        result = digest._parse_date(entry)
        self.assertEqual(result, datetime(2025, 5, 1, 12, 0, 0, tzinfo=timezone.utc))

    def test_falls_back_to_updated_parsed(self):
        entry = MockEntry(updated_parsed=_struct(datetime(2025, 4, 15, 8, 0, 0, tzinfo=timezone.utc)))
        result = digest._parse_date(entry)
        self.assertEqual(result, datetime(2025, 4, 15, 8, 0, 0, tzinfo=timezone.utc))

    def test_published_takes_priority_over_updated(self):
        pub = datetime(2025, 5, 1, tzinfo=timezone.utc)
        upd = datetime(2025, 4, 1, tzinfo=timezone.utc)
        entry = MockEntry(published_parsed=_struct(pub), updated_parsed=_struct(upd))
        self.assertEqual(digest._parse_date(entry), pub)

    def test_returns_none_when_no_dates(self):
        entry = MockEntry()
        self.assertIsNone(digest._parse_date(entry))


# ── fetch_recent_items ─────────────────────────────────────────────────────────

class TestFetchRecentItems(unittest.TestCase):
    @patch("digest.feedparser.parse")
    def test_bozo_feed_with_no_entries_warns(self, mock_parse):
        mock_parse.return_value = _mock_parsed(bozo=True, bozo_exception=Exception("timeout"))
        items, warnings = digest.fetch_recent_items([FEED], 24)
        self.assertEqual(items, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("Exception", warnings[0]["reason"])
        self.assertEqual(warnings[0]["source"], "Test Source")

    @patch("digest.feedparser.parse")
    def test_empty_feed_warns(self, mock_parse):
        mock_parse.return_value = _mock_parsed(entries=[])
        items, warnings = digest.fetch_recent_items([FEED], 24)
        self.assertEqual(items, [])
        self.assertEqual(warnings[0]["reason"], "Feed returned no entries")

    @patch("digest.feedparser.parse")
    def test_recent_item_included(self, mock_parse):
        entry = MockEntry(published_parsed=_struct(datetime.now(timezone.utc) - timedelta(hours=1)))
        mock_parse.return_value = _mock_parsed(entries=[entry])
        items, warnings = digest.fetch_recent_items([FEED], 24)
        self.assertEqual(len(items), 1)
        self.assertEqual(warnings, [])

    @patch("digest.feedparser.parse")
    def test_old_item_excluded(self, mock_parse):
        entry = MockEntry(published_parsed=_struct(datetime.now(timezone.utc) - timedelta(hours=48)))
        mock_parse.return_value = _mock_parsed(entries=[entry])
        items, warnings = digest.fetch_recent_items([FEED], 24)
        self.assertEqual(items, [])

    @patch("digest.feedparser.parse")
    def test_item_without_date_included(self, mock_parse):
        entry = MockEntry()  # no date → always include
        mock_parse.return_value = _mock_parsed(entries=[entry])
        items, _ = digest.fetch_recent_items([FEED], 24)
        self.assertEqual(len(items), 1)

    @patch("digest.feedparser.parse")
    def test_stale_feed_generates_warning(self, mock_parse):
        old = datetime.now(timezone.utc) - timedelta(days=90)
        entry = MockEntry(published_parsed=_struct(old))
        mock_parse.return_value = _mock_parsed(entries=[entry])
        _, warnings = digest.fetch_recent_items([FEED], 24)
        self.assertTrue(any("days ago" in w["reason"] for w in warnings))

    @patch("digest.feedparser.parse")
    def test_vendor_flag_propagated(self, mock_parse):
        entry = MockEntry(published_parsed=_struct(datetime.now(timezone.utc) - timedelta(hours=1)))
        mock_parse.return_value = _mock_parsed(entries=[entry])
        vendor_feed = {**FEED, "vendor": True}
        items, _ = digest.fetch_recent_items([vendor_feed], 24)
        self.assertTrue(items[0]["vendor"])

    @patch("digest.feedparser.parse")
    def test_exception_during_fetch_warns(self, mock_parse):
        mock_parse.side_effect = RuntimeError("network error")
        items, warnings = digest.fetch_recent_items([FEED], 24)
        self.assertEqual(items, [])
        self.assertIn("Exception during fetch", warnings[0]["reason"])


# ── score_and_annotate ─────────────────────────────────────────────────────────

class TestScoreAndAnnotate(unittest.TestCase):
    def _make_client(self, text):
        client = MagicMock()
        client.messages.create.return_value.content[0].text = text
        return client

    def _items(self):
        return [{"title": "Test", "summary": "Summary", "domain": "Data Engineering"}]

    def test_valid_json_scores_items(self):
        payload = json.dumps([{"id": 0, "score": 8, "editorial_note": "Great read"}])
        items = self._items()
        digest.score_and_annotate(items, self._make_client(payload))
        self.assertEqual(items[0]["score"], 8)
        self.assertEqual(items[0]["editorial_note"], "Great read")

    def test_strips_markdown_fences(self):
        payload = "```json\n" + json.dumps([{"id": 0, "score": 7, "editorial_note": "Good"}]) + "\n```"
        items = self._items()
        digest.score_and_annotate(items, self._make_client(payload))
        self.assertEqual(items[0]["score"], 7)

    def test_invalid_json_falls_back_to_score_5(self):
        items = self._items()
        digest.score_and_annotate(items, self._make_client("not json"))
        self.assertEqual(items[0]["score"], 5)
        self.assertEqual(items[0]["editorial_note"], "")

    def test_missing_id_in_response_defaults_score_0(self):
        payload = json.dumps([{"id": 99, "score": 9, "editorial_note": "Wrong id"}])
        items = self._items()
        digest.score_and_annotate(items, self._make_client(payload))
        self.assertEqual(items[0]["score"], 0)

    def test_empty_items_skips_api_call(self):
        client = MagicMock()
        digest.score_and_annotate([], client)
        client.messages.create.assert_not_called()

    def test_returns_none(self):
        payload = json.dumps([{"id": 0, "score": 6, "editorial_note": "Ok"}])
        result = digest.score_and_annotate(self._items(), self._make_client(payload))
        self.assertIsNone(result)


# ── render_email ───────────────────────────────────────────────────────────────

def _make_item(**overrides):
    base = {
        "title": "Test Article",
        "url": "https://example.com/article",
        "source": "Test Source",
        "domain": "Data Engineering",
        "published": "May 01",
        "score": 8,
        "vendor": False,
        "editorial_note": "Good read",
    }
    return {**base, **overrides}


class TestRenderEmail(unittest.TestCase):
    def test_vendor_badge_present_when_vendor_true(self):
        _, body = digest.render_email([_make_item(vendor=True)], [])
        self.assertIn("vendor source", body)

    def test_no_vendor_badge_when_vendor_false(self):
        _, body = digest.render_email([_make_item(vendor=False)], [])
        # Footer contains "†vendor source" as legend; badge span should not appear
        self.assertNotIn('font-weight:600;">vendor source</span>', body)

    def test_warnings_section_rendered(self):
        warnings = [{"source": "Bad Feed", "reason": "Feed returned no entries"}]
        _, body = digest.render_email([_make_item()], warnings)
        self.assertIn("Feed Health Warnings", body)
        self.assertIn("Bad Feed", body)
        self.assertIn("Feed returned no entries", body)

    def test_no_warnings_section_when_empty(self):
        _, body = digest.render_email([_make_item()], [])
        self.assertNotIn("Feed Health Warnings", body)

    def test_xss_in_title_escaped(self):
        _, body = digest.render_email([_make_item(title="<script>alert('xss')</script>")], [])
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_xss_in_editorial_note_escaped(self):
        _, body = digest.render_email([_make_item(editorial_note="<img onerror=alert(1)>")], [])
        self.assertNotIn("<img", body)
        self.assertIn("&lt;img", body)

    def test_xss_in_warning_reason_escaped(self):
        warnings = [{"source": "Feed", "reason": "<script>bad</script>"}]
        _, body = digest.render_email([_make_item()], warnings)
        self.assertNotIn("<script>bad</script>", body)
        self.assertIn("&lt;script&gt;", body)

    def test_javascript_url_replaced_with_hash(self):
        _, body = digest.render_email([_make_item(url="javascript:alert(1)")], [])
        self.assertNotIn('href="javascript:', body)
        self.assertIn('href="#"', body)

    def test_subject_contains_count(self):
        subject, _ = digest.render_email([_make_item()], [])
        self.assertIn("1 items", subject)


if __name__ == "__main__":
    unittest.main()
