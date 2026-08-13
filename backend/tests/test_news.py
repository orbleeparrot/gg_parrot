"""오늘의 코인동향 — RSS 파서(순수 함수) 검증. 네트워크/AI는 테스트하지 않는다."""
from __future__ import annotations

from app import news

_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <item>
    <title>비트코인 규제 논의 본격화 - 한국경제</title>
    <link>https://news.example.com/a</link>
    <pubDate>Wed, 30 Jul 2026 08:00:00 GMT</pubDate>
    <source url="https://hankyung.com">한국경제</source>
  </item>
  <item>
    <title>가상자산 이용자 보호법 시행 - 코인데스크코리아</title>
    <link>https://news.example.com/b</link>
    <pubDate>Wed, 30 Jul 2026 06:30:00 GMT</pubDate>
    <source url="https://coindeskkorea.com">코인데스크코리아</source>
  </item>
  <item>
    <title>비트코인 규제 논의 본격화 - 다른매체</title>
    <link>https://news.example.com/dup</link>
    <pubDate>Wed, 30 Jul 2026 05:00:00 GMT</pubDate>
    <source url="https://x.com">다른매체</source>
  </item>
</channel></rss>"""


def test_parse_extracts_fields_and_strips_source_suffix():
    items = news._parse_rss(_RSS)
    assert len(items) == 2  # 세 번째는 제목 중복 → 제거
    first = items[0]
    assert first["title"] == "비트코인 규제 논의 본격화"  # ' - 한국경제' 꼬리 제거
    assert first["source"] == "한국경제"
    assert first["url"] == "https://news.example.com/a"
    assert first["published"].startswith("2026-07-30")


def test_parse_dedupes_by_title():
    items = news._parse_rss(_RSS)
    titles = [i["title"] for i in items]
    assert len(titles) == len(set(titles))


def test_parse_bad_xml_returns_empty():
    assert news._parse_rss("not xml at all") == []


def test_clean_title_without_matching_source():
    assert news._clean_title("헤드라인 - 매체", None) == "헤드라인"
    assert news._clean_title("대시-없는제목", None) == "대시-없는제목"


def test_limit_is_respected():
    assert len(news._parse_rss(_RSS, limit=1)) == 1


def test_coin_news_reuses_short_ttl_cache_and_refreshes_after_expiry(monkeypatch):
    news._coin_cache.clear()
    calls = []
    item = {
        "title": "비트코인 새 소식",
        "source": "테스트 매체",
        "url": "https://news.example.com/btc",
        "published": "2026-08-12T05:00:00+00:00",
        "published_display": "10분 전",
    }

    def fake_fetch(query, *, limit):
        calls.append((query, limit))
        return [item]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    first = news.get_coin_news("BTCUSDT")
    second = news.get_coin_news("BTCUSDT")

    assert first == second
    assert first["refresh_seconds"] == news._COIN_CACHE_SECONDS
    assert len(calls) == 1

    payload, _expires_at = news._coin_cache["coin:BTC"]
    news._coin_cache["coin:BTC"] = (payload, 0)
    news.get_coin_news("BTCUSDT")
    assert len(calls) == 2
