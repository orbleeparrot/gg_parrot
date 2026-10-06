"""Publisher URL identity, without fetching links or discarding article-ID queries."""
import re
from urllib.parse import urlsplit

from .news_archive import _normalized_url


def news_identity(item: dict) -> str:
    if item.get("content_type") == "community":
        return "|".join(("community", str(item.get("source") or "Binance Square"),
                         str(item.get("community_post_id") or item.get("url") or "")))
    raw = str(item.get("url") or "")
    try:
        url = _normalized_url(raw)
        fragment = urlsplit(raw).fragment
        if url and fragment.startswith(("/", "!/")):
            url += "#" + fragment  # hash-router pages are distinct articles
    except ValueError:
        url = ""
    if url:
        return "article-url|" + url
    return "|".join((re.sub(r"\s+", " ", str(item.get("original_title") or item.get("title") or "")).strip().casefold(),
                     str(item.get("source") or ""))).strip("|")


def dedupe_news_items(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        identity = news_identity(item)
        if identity not in seen:
            seen.add(identity)
            result.append(item)
    return result
