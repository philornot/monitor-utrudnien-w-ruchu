"""Tests for wtp_monitor.py.

Run with:
    .venv/bin/pip install pytest
    .venv/bin/pytest test_wtp_monitor.py -v
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

import wtp_monitor


# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------

SAMPLE_RSS = textwrap.dedent("""\
    <?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>WTP Utrudnienia</title>
        <item>
          <guid>https://www.wtp.waw.pl/?p=1001</guid>
          <title>Wstrzymany ruch metra linii M1</title>
          <description>&lt;p&gt;Awaria pociągu między &lt;b&gt;Kabaty&lt;/b&gt; a Służew.&lt;/p&gt;</description>
          <link>https://www.wtp.waw.pl/utrudnienia/m1-awaria/</link>
          <pubDate>Tue, 13 May 2026 14:00:00 +0200</pubDate>
        </item>
        <item>
          <guid>https://www.wtp.waw.pl/?p=1002</guid>
          <title>Zmiana trasy autobusu 521</title>
          <description>&lt;p&gt;Objazd przez ul. Puławską.&lt;/p&gt;</description>
          <link>https://www.wtp.waw.pl/utrudnienia/521-objazd/</link>
          <pubDate>Tue, 13 May 2026 10:00:00 +0200</pubDate>
        </item>
      </channel>
    </rss>
""")

SAMPLE_ITEM = {
    "id": "https://www.wtp.waw.pl/?p=1001",
    "title": "Wstrzymany ruch metra linii M1",
    "description": "<p>Awaria pociągu między <b>Kabaty</b> a Służew.</p>",
    "link": "https://www.wtp.waw.pl/utrudnienia/m1-awaria/",
    "pub_date": "Tue, 13 May 2026 14:00:00 +0200",
}


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

class TestLoadConfig:
    def test_valid_config(self, tmp_path: Path) -> None:
        """Valid config file is parsed into the expected dict."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "ntfy_topic: my-topic\npoll_interval_seconds: 60\nkeywords:\n  - M1\n",
            encoding="utf-8",
        )
        cfg = wtp_monitor.load_config(cfg_file)
        assert cfg["ntfy_topic"] == "my-topic"
        assert cfg["poll_interval_seconds"] == 60
        assert cfg["keywords"] == ["M1"]

    def test_missing_file_exits(self, tmp_path: Path) -> None:
        """Missing config file causes SystemExit."""
        with pytest.raises(SystemExit):
            wtp_monitor.load_config(tmp_path / "nonexistent.yaml")

    def test_change_me_placeholder_exits(self, tmp_path: Path) -> None:
        """Unconfigured CHANGE_ME topic causes SystemExit."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("ntfy_topic: CHANGE_ME\n", encoding="utf-8")
        with pytest.raises(SystemExit):
            wtp_monitor.load_config(cfg_file)

    def test_empty_keywords_allowed(self, tmp_path: Path) -> None:
        """Empty keywords list is valid — means notify on all items."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("ntfy_topic: my-topic\nkeywords: []\n", encoding="utf-8")
        cfg = wtp_monitor.load_config(cfg_file)
        assert cfg["keywords"] == []

    def test_default_poll_interval(self, tmp_path: Path) -> None:
        """poll_interval_seconds defaults to 300 when omitted."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("ntfy_topic: my-topic\n", encoding="utf-8")
        cfg = wtp_monitor.load_config(cfg_file)
        assert cfg["poll_interval_seconds"] == 300


# ---------------------------------------------------------------------------
# _strip_html
# ---------------------------------------------------------------------------

class TestStripHtml:
    def test_removes_tags(self) -> None:
        assert wtp_monitor._strip_html("<p>Hello <b>world</b></p>") == "Hello\nworld"

    def test_decodes_html_entities(self) -> None:
        assert wtp_monitor._strip_html("Kabaty &amp; Służew") == "Kabaty & Służew"

    def test_plain_text_unchanged(self) -> None:
        assert wtp_monitor._strip_html("No tags here") == "No tags here"

    def test_empty_string(self) -> None:
        assert wtp_monitor._strip_html("") == ""

    def test_only_tags(self) -> None:
        assert wtp_monitor._strip_html("<br/><hr/>") == ""


# ---------------------------------------------------------------------------
# _format_notification_message
# ---------------------------------------------------------------------------

class TestFormatNotificationMessage:
    def test_includes_stripped_description(self) -> None:
        msg = wtp_monitor._format_notification_message(SAMPLE_ITEM)
        assert "Awaria pociągu" in msg
        assert "<p>" not in msg

    def test_includes_pub_date(self) -> None:
        msg = wtp_monitor._format_notification_message(SAMPLE_ITEM)
        assert "2026" in msg

    def test_truncates_long_description(self) -> None:
        long_item = {**SAMPLE_ITEM, "description": "A" * 500}
        msg = wtp_monitor._format_notification_message(long_item, max_desc_chars=300)
        desc_part = msg.split("\n\n")[0]
        assert desc_part.endswith("…")
        # The description portion should not exceed the limit (plus ellipsis).
        desc_part = msg.split("\n\n")[0]
        assert len(desc_part) <= 301  # 300 chars + "…"

    def test_falls_back_to_title_when_no_description(self) -> None:
        item = {**SAMPLE_ITEM, "description": "", "pub_date": ""}
        msg = wtp_monitor._format_notification_message(item)
        assert msg == SAMPLE_ITEM["title"]


# ---------------------------------------------------------------------------
# fetch_rss_items
# ---------------------------------------------------------------------------

class TestFetchRssItems:
    def test_parses_two_items(self) -> None:
        mock_response = MagicMock()
        mock_response.content = SAMPLE_RSS.encode("utf-8")
        mock_response.raise_for_status = MagicMock()

        with patch("wtp_monitor.requests.get", return_value=mock_response):
            items = wtp_monitor.fetch_rss_items("https://fake-url")

        assert len(items) == 2

    def test_item_fields_populated(self) -> None:
        mock_response = MagicMock()
        mock_response.content = SAMPLE_RSS.encode("utf-8")
        mock_response.raise_for_status = MagicMock()

        with patch("wtp_monitor.requests.get", return_value=mock_response):
            items = wtp_monitor.fetch_rss_items("https://fake-url")

        first = items[0]
        assert first["id"] == "https://www.wtp.waw.pl/?p=1001"
        assert first["title"] == "Wstrzymany ruch metra linii M1"
        assert "Kabaty" in first["description"]
        assert first["link"].startswith("https://")
        assert "2026" in first["pub_date"]

    def test_http_error_propagates(self) -> None:
        import requests as req
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError("404")

        with patch("wtp_monitor.requests.get", return_value=mock_response):
            with pytest.raises(req.HTTPError):
                wtp_monitor.fetch_rss_items("https://fake-url")


# ---------------------------------------------------------------------------
# matches_keywords
# ---------------------------------------------------------------------------

class TestMatchesKeywords:
    def test_empty_keywords_always_matches(self) -> None:
        assert wtp_monitor.matches_keywords(SAMPLE_ITEM, []) is True

    def test_matching_keyword_in_title(self) -> None:
        assert wtp_monitor.matches_keywords(SAMPLE_ITEM, ["M1"]) is True

    def test_case_insensitive(self) -> None:
        assert wtp_monitor.matches_keywords(SAMPLE_ITEM, ["m1"]) is True
        assert wtp_monitor.matches_keywords(SAMPLE_ITEM, ["METRA"]) is True

    def test_non_matching_keyword(self) -> None:
        assert wtp_monitor.matches_keywords(SAMPLE_ITEM, ["M2", "autobus 190"]) is False

    def test_keyword_matched_in_description(self) -> None:
        assert wtp_monitor.matches_keywords(SAMPLE_ITEM, ["Kabaty"]) is True


# ---------------------------------------------------------------------------
# send_notification
# ---------------------------------------------------------------------------

class TestSendNotification:
    def test_posts_to_correct_url(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("wtp_monitor.requests.post", return_value=mock_response) as mock_post:
            wtp_monitor.send_notification(SAMPLE_ITEM, "my-topic")

        mock_post.assert_called_once()
        url = mock_post.call_args.args[0]
        assert url == "https://ntfy.sh/my-topic"

    def test_payload_contains_required_fields(self) -> None:
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("wtp_monitor.requests.post", return_value=mock_response) as mock_post:
            wtp_monitor.send_notification(SAMPLE_ITEM, "my-topic")

        headers = mock_post.call_args.kwargs["headers"]
        assert b"M1" in headers["Title"]
        assert headers["Priority"] == "high"
        assert headers["Click"] == SAMPLE_ITEM["link"]

    def test_http_error_propagates(self) -> None:
        import requests as req
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError("500")

        with patch("wtp_monitor.requests.post", return_value=mock_response):
            with pytest.raises(req.HTTPError):
                wtp_monitor.send_notification(SAMPLE_ITEM, "my-topic")


# ---------------------------------------------------------------------------
# run_check (integration)
# ---------------------------------------------------------------------------

class TestRunCheck:
    BASE_CONFIG = {
        "ntfy_topic": "test-topic",
        "poll_interval_seconds": 300,
        "keywords": ["M1"],
    }

    def _make_mock_response(self, content: bytes) -> MagicMock:
        mock_response = MagicMock()
        mock_response.content = content
        mock_response.raise_for_status = MagicMock()
        return mock_response

    def test_new_matching_item_triggers_notification(self) -> None:
        notified: set = set()

        with patch("wtp_monitor.requests.get",
                   return_value=self._make_mock_response(SAMPLE_RSS.encode())), \
             patch("wtp_monitor.requests.post",
                   return_value=self._make_mock_response(b"")) as mock_post, \
             patch("wtp_monitor.save_state"):
            wtp_monitor.run_check(self.BASE_CONFIG, notified)

        # Only the M1 item should have triggered a notification.
        mock_post.assert_called_once()
        assert "https://www.wtp.waw.pl/?p=1001" in notified

    def test_already_notified_item_is_skipped(self) -> None:
        already_seen = {"https://www.wtp.waw.pl/?p=1001"}

        with patch("wtp_monitor.requests.get",
                   return_value=self._make_mock_response(SAMPLE_RSS.encode())), \
             patch("wtp_monitor.requests.post",
                   return_value=self._make_mock_response(b"")) as mock_post, \
             patch("wtp_monitor.save_state"):
            wtp_monitor.run_check(self.BASE_CONFIG, already_seen)

        mock_post.assert_not_called()

    def test_non_matching_item_skipped(self) -> None:
        config = {**self.BASE_CONFIG, "keywords": ["tramwaj"]}
        notified: set = set()

        with patch("wtp_monitor.requests.get",
                   return_value=self._make_mock_response(SAMPLE_RSS.encode())), \
             patch("wtp_monitor.requests.post",
                   return_value=self._make_mock_response(b"")) as mock_post, \
             patch("wtp_monitor.save_state"):
            wtp_monitor.run_check(config, notified)

        mock_post.assert_not_called()
        assert len(notified) == 0

    def test_rss_fetch_failure_does_not_crash(self) -> None:
        """A network error during RSS fetch should be logged, not raised."""
        import requests as req
        notified: set = set()

        with patch("wtp_monitor.requests.get", side_effect=req.ConnectionError("timeout")), \
             patch("wtp_monitor.save_state"):
            # Should not raise.
            wtp_monitor.run_check(self.BASE_CONFIG, notified)

    def test_state_is_saved_after_check(self) -> None:
        notified: set = set()

        with patch("wtp_monitor.requests.get",
                   return_value=self._make_mock_response(SAMPLE_RSS.encode())), \
             patch("wtp_monitor.requests.post",
                   return_value=self._make_mock_response(b"")), \
             patch("wtp_monitor.save_state") as mock_save:
            wtp_monitor.run_check(self.BASE_CONFIG, notified)

        mock_save.assert_called_once()


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

class TestStatePersistence:
    def test_save_and_load_roundtrip(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(wtp_monitor, "STATE_FILE", tmp_path / "state.json")

        original = {"id-001", "id-002", "id-003"}
        wtp_monitor.save_state(original)
        loaded = wtp_monitor.load_state()
        assert loaded == original

    def test_load_returns_empty_set_when_file_missing(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(wtp_monitor, "STATE_FILE", tmp_path / "no_such_file.json")
        assert wtp_monitor.load_state() == set()

    def test_save_trims_to_max_entries(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(wtp_monitor, "STATE_FILE", tmp_path / "state.json")
        monkeypatch.setattr(wtp_monitor, "MAX_STATE_ENTRIES", 5)

        big_set = {f"id-{i:04d}" for i in range(20)}
        wtp_monitor.save_state(big_set)

        data = json.loads((tmp_path / "state.json").read_text())
        assert len(data["notified_ids"]) == 5