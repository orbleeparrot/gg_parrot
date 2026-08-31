"""오늘의 코인동향 — RSS 파서(순수 함수) 검증. 네트워크/AI는 테스트하지 않는다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

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

_COINDESK_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>CoinDesk: Bitcoin, Ethereum, Crypto News and Price Data</title>
  <item>
    <title>OpenEden expands its tokenized Treasury platform</title>
    <link>https://www.coindesk.com/business/2026/08/25/openeden-expands</link>
    <pubDate>Tue, 25 Aug 2026 01:00:00 +0000</pubDate>
    <category>Finance</category>
    <category>Tokenization</category>
  </item>
  <item>
    <title>Strategy raises fresh cash through MSTR sales</title>
    <link>https://www.coindesk.com/markets/2026/08/25/strategy-raises-cash</link>
    <pubDate>Tue, 25 Aug 2026 00:30:00 +0000</pubDate>
    <category>Markets</category>
    <category>Bitcoin News</category>
  </item>
  <item>
    <title>Ethereum upgrade changes wallet gas assumptions</title>
    <link>https://www.coindesk.com/tech/2026/08/25/ethereum-upgrade</link>
    <pubDate>Tue, 25 Aug 2026 00:00:00 +0000</pubDate>
    <category>Tech</category>
    <category>Ethereum News</category>
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

    def fake_fetch(query, *, limit, **_kwargs):
        calls.append((query, limit))
        return [item]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _symbol: None)
    first = news.get_coin_news("BTCUSDT")
    second = news.get_coin_news("BTCUSDT")

    assert first == second
    assert first["refresh_seconds"] == news._COIN_CACHE_SECONDS
    assert len(calls) == 1

    payload, _expires_at = news._coin_cache["coin:BTC"]
    news._coin_cache["coin:BTC"] = (payload, 0)
    news.get_coin_news("BTCUSDT")
    assert len(calls) == 2


def test_market_news_retries_transient_ai_summary_without_refetching_headlines(monkeypatch):
    item = {
        "title": "비트코인 시장 새 소식",
        "source": "테스트 매체",
        "url": "https://news.example.com/market",
        "published": "2026-08-26T01:00:00+00:00",
        "published_display": "10분 전",
    }
    fetch_calls = []
    summary_calls = []
    summaries = iter([None, "시장 뉴스 요약"])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(news, "_cache", {})
    monkeypatch.setattr(news, "_MARKET_SUMMARY_RETRY_SECONDS", 0, raising=False)
    monkeypatch.setattr(news, "_market_summary_retry_at", 0.0, raising=False)
    monkeypatch.setattr(news, "_kst_date", lambda: "2026-08-26")

    def fetch(query, **_kwargs):
        fetch_calls.append(query)
        return [item]

    def summarize(items, *, label):
        summary_calls.append((items, label))
        return next(summaries)

    monkeypatch.setattr(news, "_fetch_news", fetch)
    monkeypatch.setattr(news, "_summarize", summarize)

    first = news.get_market_news()
    second = news.get_market_news()

    assert first["items"] == [item]
    assert first["overview"] is None
    assert second["overview"] == "시장 뉴스 요약"
    assert len(fetch_calls) == 1
    assert len(summary_calls) == 2


def test_coin_news_prefers_central_snapshot_for_collected_asset(monkeypatch):
    item = {
        "title": "중앙 수집기가 저장한 비트코인 소식",
        "source": "테스트 매체",
        "url": "https://news.example.com/prefect-btc",
        "published": "2026-08-24T04:00:00+00:00",
        "published_display": "10분 전",
    }
    stored = {
        "snapshot_id": "btc-snapshot-1",
        "news_payload": {
            "symbol": "BTC",
            "coin_name": "비트코인",
            "label": "비트코인 뉴스",
            "items": [item],
            "updated_at": "2026-08-24T04:10:00Z",
            "refresh_seconds": 300,
        },
        "collection": {
            "status": "ready",
            "last_success_at": "2026-08-24T04:10:00Z",
            "last_success_ms": 1_777_173_000_000,
        },
    }
    loaded = []

    def fake_load(symbol):
        loaded.append(symbol)
        return stored

    def unexpected_rss(*_args, **_kwargs):
        raise AssertionError("중앙 스냅샷이 있으면 RSS를 호출하면 안 됩니다")

    monkeypatch.setattr(
        news,
        "_load_latest_coin_snapshot",
        fake_load,
        raising=False,
    )
    monkeypatch.setattr(news, "_fetch_news", unexpected_rss)

    payload = news.get_coin_news("BTCUSDT")

    assert loaded == ["BTC"]
    assert payload["items"] == [item]
    assert payload["data_source"] == "prefect_db"
    assert payload["snapshot_id"] == "btc-snapshot-1"
    assert payload["collection"]["status"] == "ready"


def test_coin_news_uses_rss_cache_for_uncollected_asset(monkeypatch):
    news._coin_cache.clear()
    item = {
        "title": "미지원 티커의 새 소식",
        "source": "테스트 매체",
        "url": "https://news.example.com/crv",
        "published": "2026-08-24T04:00:00+00:00",
        "published_display": "10분 전",
    }

    def unexpected_db(_symbol):
        raise AssertionError("수집 대상이 아닌 티커는 DB를 조회하면 안 됩니다")

    monkeypatch.setattr(news, "_load_latest_coin_snapshot", unexpected_db)
    monkeypatch.setattr(
        news,
        "_fetch_news",
        lambda _query, *, limit, **_kwargs: [item],
    )

    payload = news.get_coin_news("CRVUSDT")

    assert payload["symbol"] == "CRV"
    assert payload["items"] == [item]
    assert payload["data_source"] == "rss_cache"


def test_coin_news_falls_back_to_rss_when_central_store_is_unavailable(monkeypatch):
    news._coin_cache.clear()
    item = {
        "title": "DB 장애 중 RSS로 제공한 비트코인 소식",
        "source": "테스트 매체",
        "url": "https://news.example.com/btc-fallback",
        "published": "2026-08-24T04:00:00+00:00",
        "published_display": "10분 전",
    }

    def unavailable_db(_symbol):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(news, "_load_latest_coin_snapshot", unavailable_db)
    monkeypatch.setattr(
        news,
        "_fetch_news",
        lambda _query, *, limit, **_kwargs: [item],
    )

    payload = news.get_coin_news("BTCUSDT")

    assert payload["symbol"] == "BTC"
    assert payload["items"] == [item]
    assert payload["data_source"] == "rss_cache"


@pytest.mark.parametrize(
    ("market_symbol", "expected"),
    [
        ("btcusdt", "BTC"),
        ("BTCUSDC", "BTC"),
        ("ETHBUSD", "ETH"),
        ("SOL", "SOL"),
        ("BTCFDUSD", "BTC"),
        ("XRPTUSD", "XRP"),
        ("FDUSDUSDT", "FDUSD"),
        ("TUSDUSDT", "TUSD"),
        ("BUSD", "BUSD"),
        ("CRVUSDUSDT", "CRVUSD"),
        ("PYUSDUSDT", "PYUSD"),
        ("GUSDUSDT", "GUSD"),
        ("SUSDUSDT", "SUSD"),
        ("PAXUSDUSDT", "PAXUSD"),
        ("A" * 15 + "USDT", "A" * 15),
        ("../BTCUSDT", ""),
        ("A", ""),
    ],
)
def test_asset_from_market_symbol_strips_one_quote_at_ingress(market_symbol, expected):
    assert news.asset_from_market_symbol(market_symbol) == expected


def test_worker_fetch_surfaces_network_failure(monkeypatch):
    def broken_client(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(news.httpx, "Client", broken_client)
    assert news._fetch_news("BTC", strict=False) == []
    with pytest.raises(news.NewsFetchError):
        news._fetch_news("BTC", strict=True)


def test_worker_merges_coindesk_rss_and_filters_each_source_by_asset(monkeypatch):
    google_items = [
        {
            "title": "오픈에덴, 토큰화 국채 상품 확대",
            "source": "테스트뉴스",
            "url": "https://news.example.com/openeden",
            "published": "2026-08-25T01:10:00+00:00",
            "published_display": "10분 전",
        },
        {
            "title": "비트코인 8만 달러 접근",
            "source": "테스트뉴스",
            "url": "https://news.example.com/bitcoin",
            "published": "2026-08-25T01:05:00+00:00",
            "published_display": "15분 전",
        },
    ]
    calls = []

    class FakeResponse:
        text = _COINDESK_RSS

        @staticmethod
        def raise_for_status():
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(news, "_coindesk_cache", None, raising=False)
    monkeypatch.setattr(news, "_fetch_news", lambda *_args, **_kwargs: google_items)
    monkeypatch.setattr(
        news,
        "_fetch_openeden_news",
        lambda **_kwargs: [],
        raising=False,
    )
    monkeypatch.setattr(news.httpx, "Client", lambda **_kwargs: FakeClient())

    payload = news.fetch_coin_news_for_collector("EDEN")

    assert [item["title"] for item in payload["items"]] == [
        "OpenEden expands its tokenized Treasury platform",
        "오픈에덴, 토큰화 국채 상품 확대",
    ]
    assert payload["items"][0]["source"] == "CoinDesk"
    assert payload["items"][0]["categories"] == ["Finance", "Tokenization"]
    assert payload["items"][0]["feed_source"] == "coindesk_rss"
    assert payload["items"][1]["feed_source"] == "google_news_rss"
    assert {
        source["name"]: (source["status"], source["item_count"])
        for source in payload["sources"]
    } == {
        "openeden_official_rss": ("ready", 0),
        "coindesk_rss": ("ready", 1),
        "google_news_rss": ("ready", 1),
    }
    assert calls == ["https://www.coindesk.com/arc/outboundfeeds/rss/"]


def test_eden_uses_korean_and_english_google_queries(monkeypatch):
    korean = {
        "title": "오픈에덴, 이틀간 하락 딛고 22% 급등",
        "source": "뉴스1",
        "url": "https://news.example.com/openeden-ko",
        "published": "2026-08-18T07:37:14+00:00",
        "published_display": "7일 전",
    }
    dealroom = {
        "title": "Ripple, Lightspeed back OpenEden to scale tokenized Treasurys",
        "source": "Dealroom",
        "url": "https://news.example.com/openeden-dealroom",
        "published": "2026-08-16T23:16:05+00:00",
        "published_display": "8일 전",
    }
    market = {
        "title": "OpenEden Price Prediction: Why Is $EDEN Up 78% Today?",
        "source": "Coin Gabbar",
        "url": "https://news.example.com/openeden-market",
        "published": "2026-08-14T06:40:00+00:00",
        "published_display": "10일 전",
    }
    ticker_market = {
        "title": "EDEN coin gains after a new exchange listing",
        "source": "Crypto Desk",
        "url": "https://news.example.com/eden-coin-listing",
        "published": "2026-08-13T06:40:00+00:00",
        "published_display": "11일 전",
    }
    unrelated_korean = [
        {
            "title": f"알트코인 시장 소식 {index}",
            "source": "시장뉴스",
            "url": f"https://news.example.com/market-ko-{index}",
            "published": "2026-08-20T00:00:00+00:00",
            "published_display": "5일 전",
        }
        for index in range(3)
    ]
    unrelated_english = [
        {
            "title": f"Unrelated crypto market update {index}",
            "source": "Market News",
            "url": f"https://news.example.com/market-en-{index}",
            "published": "2026-08-20T00:00:00+00:00",
            "published_display": "5일 전",
        }
        for index in range(5)
    ]
    calls = []

    def fake_fetch(query, *, limit, strict, locale="ko"):
        calls.append((query, limit, strict, locale))
        if locale == "ko":
            return [korean, *unrelated_korean][:limit]
        if query.startswith("(OpenEden OR"):
            return [dealroom, *unrelated_english, market][:limit]
        return [
            {
                "title": "Eden Prairie opens a new community center",
                "source": "Local News",
                "url": "https://news.example.com/eden-prairie",
                "published": "2026-08-13T05:00:00+00:00",
                "published_display": "11일 전",
            },
            ticker_market,
        ][:limit]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_kwargs: [])
    monkeypatch.setattr(
        news,
        "_fetch_openeden_news",
        lambda **_kwargs: [],
        raising=False,
    )

    payload = news.fetch_coin_news_for_collector("EDEN")

    assert "EDEN" in news.position_news_collection_universe()
    assert [item["title"] for item in payload["items"]] == [
        korean["title"],
        dealroom["title"],
        ticker_market["title"],
        market["title"],
    ]
    assert payload["query"] == (
        '(OpenEden OR 오픈에덴 OR "EDEN 코인") when:30d | '
        '(OpenEden OR "Open Eden") when:30d | '
        '(EDEN coin OR EDEN crypto OR EDEN token OR $EDEN OR EDEN USDT OR '
        'EDEN listing OR EDEN price) when:30d'
    )
    assert calls == [
        ('(OpenEden OR 오픈에덴 OR "EDEN 코인") when:30d', 50, True, "ko"),
        (
            '(OpenEden OR "Open Eden") when:30d',
            50,
            True,
            "en",
        ),
        (
            '(EDEN coin OR EDEN crypto OR EDEN token OR $EDEN OR EDEN USDT OR '
            'EDEN listing OR EDEN price) when:30d',
            50,
            True,
            "en",
        ),
    ]
    google_source = next(
        source
        for source in payload["sources"]
        if source["name"] == "google_news_rss"
    )
    assert google_source["item_count"] == 4
    assert google_source["fetched_count"] == 13


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("OpenEden expands its tokenized Treasury platform", True),
        ("Why Is $EDEN Up 78% Today?", True),
        ("EDEN coin gains after a new exchange listing", True),
        ("6 Best Cryptos To Buy Now as RED, EDEN and HEMI Jump", True),
        ("Magic Eden drops Bitcoin and Ethereum support", False),
        ("Eden Research (EDEN) gains regulatory approval", False),
        ("Eden Prairie opens a new community center", False),
        ("East of Eden gets a Netflix release date", False),
        ("Another Eden game announces a new chapter", False),
        ("Eden Project opens a new exhibition", False),
        ("Eden targets two huge concrete markets", False),
        ("Eden Innovations notifies holders of listed options", False),
        ("iShares MSCI Denmark ETF (EDEN) market update", False),
        ("Liquidity Mapping Around (EDEN) Price Events", False),
        ("Eden Price Today | EDEN to INR Live Price and Chart", False),
    ],
)
def test_eden_relevance_keeps_crypto_ticker_context_without_generic_eden_noise(
    title,
    expected,
):
    item = {"title": title, "categories": []}

    assert news._matches_asset(item, "EDEN", "오픈에덴") is expected


def test_eden_includes_recent_official_openeden_rss_and_drops_old_items(monkeypatch):
    recent = datetime.now(timezone.utc) - timedelta(days=3)
    old = datetime.now(timezone.utc) - timedelta(days=60)
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>OpenEden launches a new tokenized bond product</title>
        <link>https://openeden.com/news/new-tokenized-bond/</link>
        <pubDate>{format_datetime(recent)}</pubDate>
      </item>
      <item>
        <title>OpenEden old archive update</title>
        <link>https://openeden.com/news/old-archive/</link>
        <pubDate>{format_datetime(old)}</pubDate>
      </item>
    </channel></rss>"""
    calls = []

    class FakeResponse:
        text = rss

        @staticmethod
        def raise_for_status():
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(news, "_openeden_cache", None, raising=False)
    monkeypatch.setattr(news, "_fetch_news", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_kwargs: [])
    monkeypatch.setattr(news.httpx, "Client", lambda **_kwargs: FakeClient())

    payload = news.fetch_coin_news_for_collector("EDEN")

    assert [item["title"] for item in payload["items"]] == [
        "OpenEden launches a new tokenized bond product",
    ]
    assert payload["items"][0]["source"] == "OpenEden"
    assert payload["items"][0]["feed_source"] == "openeden_official_rss"
    assert {
        source["name"]: (source["status"], source["item_count"])
        for source in payload["sources"]
    }["openeden_official_rss"] == ("ready", 1)
    assert calls == ["https://openeden.com/news/feed/"]


def test_coindesk_feed_is_shared_across_tickers_and_uses_category_tags(monkeypatch):
    calls = []

    class FakeResponse:
        text = _COINDESK_RSS

        @staticmethod
        def raise_for_status():
            return None

    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, **_kwargs):
            calls.append(url)
            return FakeResponse()

    monkeypatch.setattr(news, "_coindesk_cache", None, raising=False)
    monkeypatch.setattr(news, "_fetch_news", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(news.httpx, "Client", lambda **_kwargs: FakeClient())

    bitcoin = news.fetch_coin_news_for_collector("BTC")
    ethereum = news.fetch_coin_news_for_collector("ETH")

    assert [item["title"] for item in bitcoin["items"]] == [
        "Strategy raises fresh cash through MSTR sales",
    ]
    assert [item["title"] for item in ethereum["items"]] == [
        "Ethereum upgrade changes wallet gas assumptions",
    ]
    assert calls == ["https://www.coindesk.com/arc/outboundfeeds/rss/"]


def test_news_merge_keeps_source_diversity_when_one_feed_exceeds_limit(monkeypatch):
    monkeypatch.setattr(news, "_MAX_COIN_ITEMS", 4)
    coindesk = [
        {"title": f"CoinDesk {index}", "feed_source": "coindesk_rss"}
        for index in range(4)
    ]
    google = [
        {"title": f"Google {index}", "feed_source": "google_news_rss"}
        for index in range(4)
    ]

    merged = news._merge_news_items(coindesk, google)

    assert [item["feed_source"] for item in merged] == [
        "coindesk_rss",
        "google_news_rss",
        "coindesk_rss",
        "google_news_rss",
    ]


@pytest.mark.parametrize(
    "symbol",
    [
        "BTC",
        "BUSD",
        "TUSD",
        "FDUSD",
        "CRVUSD",
        "PYUSD",
        "GUSD",
        "SUSD",
        "PAXUSD",
        "A" * 15,
    ],
)
def test_canonical_asset_symbol_preserves_valid_base_and_is_idempotent(symbol):
    once = news.canonical_asset_symbol(symbol)
    assert once == symbol
    assert news.canonical_asset_symbol(once) == once


@pytest.mark.parametrize("symbol", ["A", "A" * 16, "../BTC", "BTC-USDT"])
def test_canonical_asset_symbol_rejects_out_of_bounds_or_unsafe_base(symbol):
    assert news.canonical_asset_symbol(symbol) == ""
