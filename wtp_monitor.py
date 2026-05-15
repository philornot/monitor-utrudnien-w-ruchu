"""
WTP Disruption Monitor
=======================
Polls the WTP RSS feed on a fixed interval and sends a push notification
via ntfy.sh when a matching disruption is detected.

Behaviour is controlled entirely by ``config.yaml`` in the same directory:
  - ``ntfy_topic``            — ntfy.sh topic to publish to
  - ``poll_interval_seconds`` — how often to poll (default: 300)
  - ``keywords``              — list of strings to match; empty list = notify on ALL items

Designed to run as a long-lived systemd service. Handles SIGTERM gracefully.
"""

import hashlib
import html
import html.parser
import json
import logging
import signal
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).parent
CONFIG_FILE = PROJECT_DIR / "config.yaml"
STATE_FILE = PROJECT_DIR / ".monitor_state.json"

WTP_RSS_URL = "https://www.wtp.waw.pl/feed/?post_type=impediment"

# Maximum number of item IDs to keep in the state file.
MAX_STATE_ENTRIES = 200

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    """Load and validate configuration from a YAML file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        A dict with keys ``ntfy_topic`` (str), ``poll_interval_seconds`` (int),
        and ``keywords`` (list[str]).

    Raises:
        SystemExit: If the file is missing, malformed, or ``ntfy_topic`` is
            still set to the default placeholder.
    """
    if not path.exists():
        log.error("Config file not found: %s", path)
        sys.exit(1)

    try:
        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        log.error("Failed to parse config.yaml: %s", exc)
        sys.exit(1)

    if not isinstance(raw, dict):
        log.error("config.yaml must be a YAML mapping at the top level.")
        sys.exit(1)

    topic = raw.get("ntfy_topic", "")
    if not topic or "CHANGE_ME" in str(topic):
        log.error(
            "ntfy_topic is not configured. "
            "Edit config.yaml, set a unique topic, then subscribe to it in the ntfy app."
        )
        sys.exit(1)

    keywords = raw.get("keywords") or []
    if not isinstance(keywords, list):
        log.error("'keywords' in config.yaml must be a list (or empty).")
        sys.exit(1)

    interval = int(raw.get("poll_interval_seconds", 300))

    config = {
        "ntfy_topic": str(topic),
        "poll_interval_seconds": interval,
        "keywords": [str(k) for k in keywords],
    }

    if config["keywords"]:
        log.info("Filtering by keywords: %s", config["keywords"])
    else:
        log.info("No keywords set — will notify on ALL disruptions.")

    return config


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

class _ShutdownFlag:
    """Simple flag set by a signal handler to request a clean exit."""

    def __init__(self):
        self._stop = False
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum, frame):
        log.info("Received signal %s — shutting down.", signum)
        self._stop = True

    @property
    def is_set(self) -> bool:
        """Return True if a shutdown signal has been received."""
        return self._stop


# ---------------------------------------------------------------------------
# State helpers
# ---------------------------------------------------------------------------

def load_state() -> set:
    """Load the set of already-notified item IDs from the state file.

    Returns:
        A set of item ID strings that have already triggered a notification.
    """
    if not STATE_FILE.exists():
        return set()
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return set(data.get("notified_ids", []))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Could not read state file (%s) — starting fresh.", exc)
        return set()


def save_state(notified_ids: set) -> None:
    """Persist the set of notified item IDs to the state file.

    Args:
        notified_ids: The updated set of item IDs to save.
    """
    trimmed = list(notified_ids)[-MAX_STATE_ENTRIES:]
    try:
        STATE_FILE.write_text(
            json.dumps({"notified_ids": trimmed}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        log.error("Could not save state file: %s", exc)


# ---------------------------------------------------------------------------
# RSS parsing
# ---------------------------------------------------------------------------

def fetch_rss_items(url: str, timeout: int = 15) -> list[dict]:
    """Fetch and parse the WTP RSS feed.

    Args:
        url: The RSS feed URL.
        timeout: HTTP request timeout in seconds.

    Returns:
        A list of dicts, each with keys: ``id``, ``title``, ``description``,
        ``link``, ``pub_date``.

    Raises:
        requests.RequestException: If the HTTP request fails.
        xml.etree.ElementTree.ParseError: If the response is not valid XML.
    """
    response = requests.get(url, timeout=timeout, headers={"User-Agent": "wtp-monitor/1.0"})
    response.raise_for_status()

    root = ET.fromstring(response.content)
    items = []

    for item in root.findall(".//item"):
        guid = (item.findtext("guid") or "").strip()
        title = (item.findtext("title") or "").strip()
        description = (item.findtext("description") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()

        # Prefer the GUID as a stable ID; fall back to a hash of title+date.
        item_id = guid or hashlib.md5(f"{title}{pub_date}".encode()).hexdigest()

        items.append({
            "id": item_id,
            "title": title,
            "description": description,
            "link": link,
            "pub_date": pub_date,
        })

    return items


# ---------------------------------------------------------------------------
# Disruption matching
# ---------------------------------------------------------------------------

def matches_keywords(item: dict, keywords: list[str]) -> bool:
    """Check whether an RSS item matches the configured keywords.

    If ``keywords`` is empty, every item is considered a match.

    Args:
        item: A parsed RSS item dict (see :func:`fetch_rss_items`).
        keywords: List of keywords to search for. Case-insensitive.

    Returns:
        ``True`` if the item should trigger a notification.
    """
    if not keywords:
        return True
    haystack = f"{item['title']} {item['description']}".lower()
    return any(kw.lower() in haystack for kw in keywords)


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def send_notification(item: dict, ntfy_topic: str) -> None:
    """Send a push notification via ntfy.sh using HTTP headers.

    Uses the ntfy header-based API instead of a JSON body, which avoids
    Content-Type negotiation issues and Unicode escaping in the payload.

    Args:
        item: The RSS item that triggered the alert.
        ntfy_topic: The ntfy.sh topic to publish to.

    Raises:
        requests.RequestException: If the ntfy.sh request fails.
    """
    message = _format_notification_message(item)
    title = f"🚨 {item['title']}"

    response = requests.post(
        f"https://ntfy.sh/{ntfy_topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": "high",
            "Tags": "warszawa,transport,warning",
            "Click": item["link"],
        },
        timeout=10,
    )
    response.raise_for_status()
    log.info("Notification sent: %s", item["title"])

# ---------------------------------------------------------------------------
# Notification formatting
# ---------------------------------------------------------------------------

class _HTMLStripper(html.parser.HTMLParser):
    """Minimal HTML parser that discards all tags and collects plain text."""

    def __init__(self):
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        stripped = data.strip()
        if stripped:
            self._parts.append(stripped)

    def get_text(self) -> str:
        """Return the accumulated plain text joined by newlines."""
        return "\n".join(self._parts)


def _strip_html(raw: str) -> str:
    """Strip HTML tags from *raw* and unescape HTML entities.

    Args:
        raw: A string that may contain HTML markup.

    Returns:
        Plain text with tags removed and entities decoded (e.g. ``&amp;`` → ``&``).
    """
    stripper = _HTMLStripper()
    stripper.feed(raw)
    return html.unescape(stripper.get_text())


def _format_notification_message(item: dict, max_desc_chars: int = 300) -> str:
    """Build a clean, human-readable notification body from an RSS item.

    Args:
        item: A parsed RSS item dict (see :func:`fetch_rss_items`).
        max_desc_chars: Maximum number of characters taken from the description
            before it is truncated with an ellipsis. Keeps ntfy previews tidy.

    Returns:
        A formatted string ready to be used as the ntfy ``message`` field.
    """
    parts: list[str] = []

    description = _strip_html(item.get("description", ""))
    if description:
        if len(description) > max_desc_chars:
            description = description[:max_desc_chars].rstrip() + "…"
        parts.append(description)

    if item.get("pub_date"):
        parts.append(f"🕐 {item['pub_date']}")

    return "\n\n".join(parts) if parts else item["title"]

# ---------------------------------------------------------------------------
# Single poll cycle
# ---------------------------------------------------------------------------

def run_check(config: dict, notified_ids: set) -> None:
    """Fetch the RSS feed and send notifications for any new matching disruptions.

    Modifies ``notified_ids`` in-place and persists state to disk.

    Args:
        config: Loaded configuration dict (see :func:`load_config`).
        notified_ids: Mutable set of item IDs that were already notified.
    """
    log.info("Polling WTP RSS feed…")
    try:
        items = fetch_rss_items(WTP_RSS_URL)
    except Exception as exc:
        log.error("Failed to fetch RSS feed: %s", exc)
        return

    found_new = False
    for item in items:
        if not matches_keywords(item, config["keywords"]):
            continue
        if item["id"] in notified_ids:
            log.debug("Already notified: %s", item["title"])
            continue

        log.info("NEW disruption detected: %s", item["title"])
        try:
            send_notification(item, config["ntfy_topic"])
            notified_ids.add(item["id"])
            found_new = True
        except Exception as exc:
            log.error("Failed to send notification: %s", exc)

    save_state(notified_ids)

    if not found_new:
        log.info("No new matching disruptions.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Load config, then start the polling loop. Exits cleanly on SIGTERM/SIGINT."""
    config = load_config(CONFIG_FILE)

    log.info(
        "WTP monitor started. Topic: %s | Interval: %ds",
        config["ntfy_topic"],
        config["poll_interval_seconds"],
    )

    shutdown = _ShutdownFlag()
    notified_ids = load_state()

    while not shutdown.is_set:
        run_check(config, notified_ids)

        # Sleep in 1-second increments so SIGTERM is handled promptly.
        for _ in range(config["poll_interval_seconds"]):
            if shutdown.is_set:
                break
            time.sleep(1)

    log.info("WTP monitor stopped.")


if __name__ == "__main__":
    main()