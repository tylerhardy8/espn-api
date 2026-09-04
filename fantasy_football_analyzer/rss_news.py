"""Fantasy football RSS news aggregator.

Fetches player news from popular fantasy football RSS feeds and matches
headlines to player names for use in waiver wire recommendations.
"""

import re
import time
from html import unescape

try:
    import feedparser
    HAS_FEEDPARSER = True
except ImportError:
    HAS_FEEDPARSER = False


# Default RSS feed URLs for fantasy football news
DEFAULT_FEEDS = [
    {
        "url": "https://www.rotowire.com/rss/nfl.xml",
        "source": "RotoWire",
    },
    {
        "url": "https://www.fantasypros.com/nfl/rss/news.php",
        "source": "FantasyPros",
    },
    {
        "url": "https://www.nbcsports.com/fantasy/football/player-news?rss=1",
        "source": "Rotoworld",
    },
]

# In-memory cache: {cache_key: (items, timestamp)}
_news_cache = {}
_CACHE_TTL = 900  # 15 minutes


def _strip_html(text):
    """Remove HTML tags and decode entities from text."""
    if not text:
        return ""
    clean = re.sub(r"<[^>]+>", "", text)
    return unescape(clean).strip()


def fetch_news(feed_urls=None, max_items=30):
    """Fetch news items from RSS feeds.

    Args:
        feed_urls: list of feed config dicts with "url" and "source" keys.
                   Defaults to DEFAULT_FEEDS.
        max_items: maximum total items to return across all feeds.

    Returns:
        List of news item dicts sorted by publish date (newest first).
    """
    if not HAS_FEEDPARSER:
        return []

    feeds = feed_urls or DEFAULT_FEEDS
    cache_key = "|".join(f["url"] for f in feeds)

    # Check cache
    if cache_key in _news_cache:
        cached_items, cached_time = _news_cache[cache_key]
        if time.time() - cached_time < _CACHE_TTL:
            return cached_items[:max_items]

    all_items = []
    for feed_config in feeds:
        try:
            parsed = feedparser.parse(feed_config["url"])
            source = feed_config.get("source", "Unknown")

            for entry in parsed.entries[:15]:
                title = _strip_html(entry.get("title", ""))
                summary = _strip_html(entry.get("summary", entry.get("description", "")))

                # Truncate long summaries
                if len(summary) > 300:
                    summary = summary[:297] + "..."

                published = ""
                if hasattr(entry, "published"):
                    published = entry.published
                elif hasattr(entry, "updated"):
                    published = entry.updated

                all_items.append({
                    "title": title,
                    "summary": summary,
                    "link": entry.get("link", ""),
                    "published": published,
                    "source": source,
                })
        except Exception:
            # Skip feeds that fail to load
            continue

    # Sort by published date (newest first) — simple string sort works for
    # most standard date formats
    all_items.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Cache results
    _news_cache[cache_key] = (all_items, time.time())

    return all_items[:max_items]


def match_news_to_players(news_items, player_names):
    """Match news items to player names using simple text matching.

    Args:
        news_items: list of news dicts from fetch_news()
        player_names: list of player name strings to match against

    Returns:
        Dict of {player_name: [matching_news_items]}
    """
    if not news_items or not player_names:
        return {}

    matches = {}

    # Build lookup: for each player, check if their name appears in news
    for name in player_names:
        if not name or len(name) < 3:
            continue

        # Split into parts for matching (e.g., "Patrick Mahomes" -> check both)
        name_lower = name.lower()
        # Use last name for matching (more unique), require at least 3 chars
        parts = name.split()
        last_name = parts[-1].lower() if parts else ""

        player_news = []
        for item in news_items:
            text = (item.get("title", "") + " " + item.get("summary", "")).lower()

            # Full name match (preferred)
            if name_lower in text:
                player_news.append(item)
            # Last name match (if last name is distinctive enough, 4+ chars)
            elif len(last_name) >= 4 and last_name in text:
                # Verify it's not a common word match by checking word boundaries
                if re.search(r'\b' + re.escape(last_name) + r'\b', text):
                    player_news.append(item)

        if player_news:
            matches[name] = player_news

    return matches


def clear_news_cache():
    """Clear the cached news items."""
    _news_cache.clear()
