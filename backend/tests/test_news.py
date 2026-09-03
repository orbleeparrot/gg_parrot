"""오늘의 코인동향 — RSS 파서(순수 함수) 검증. 네트워크/AI는 테스트하지 않는다."""
from __future__ import annotations

import hashlib
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
    <description><![CDATA[OpenEden added support for more tokenized U.S. Treasury products.]]></description>
    <pubDate>Tue, 25 Aug 2026 01:00:00 +0000</pubDate>
    <category>Finance</category>
    <category>Tokenization</category>
  </item>
  <item>
    <title>Strategy raises fresh cash through MSTR sales</title>
    <link>https://www.coindesk.com/markets/2026/08/25/strategy-raises-cash</link>
    <description><![CDATA[Strategy raised new capital by selling shares of MSTR.]]></description>
    <pubDate>Tue, 25 Aug 2026 00:30:00 +0000</pubDate>
    <category>Markets</category>
    <category>Bitcoin News</category>
  </item>
  <item>
    <title>Ethereum upgrade changes wallet gas assumptions</title>
    <link>https://www.coindesk.com/tech/2026/08/25/ethereum-upgrade</link>
    <description><![CDATA[The Ethereum upgrade changes how wallets estimate gas fees.]]></description>
    <pubDate>Tue, 25 Aug 2026 00:00:00 +0000</pubDate>
    <category>Tech</category>
    <category>Ethereum News</category>
  </item>
</channel></rss>"""


def _empty_coindesk_discovery(**_kwargs):
    return {"items": [], "items_by_source": {}, "sources": []}


@pytest.fixture(autouse=True)
def _disable_real_coindesk_browser(monkeypatch):
    """Keep unit tests offline; browser-specific tests install their own fake."""
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_pages_playwright",
        lambda _descriptors: {},
        raising=False,
    )
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_asset_archive_news",
        lambda *_args, **_kwargs: [],
        raising=False,
    )


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


def test_coin_news_translates_english_snapshot_titles_and_keeps_original(monkeypatch):
    news._coin_cache.clear()
    english = "Robinhood's new crypto network sends Arbitrum's token soaring"
    korean = "로빈후드의 새 암호화폐 네트워크에 아비트럼 토큰 급등"
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "test-key")
    monkeypatch.setattr(news, "_title_translation_cache", {}, raising=False)
    monkeypatch.setattr(news, "_title_translation_budget", ("", 0), raising=False)
    monkeypatch.setattr(
        news,
        "_request_korean_title_translations",
        lambda titles: {english: korean} if titles == [english] else {},
        raising=False,
    )
    monkeypatch.setattr(
        news,
        "_load_latest_coin_snapshot",
        lambda _symbol: {
            "snapshot_id": "arb-snapshot",
            "news_payload": {
                "symbol": "ARB",
                "items": [
                    {"title": english, "source": "CoinDesk"},
                    {"title": "아비트럼 24시간 28% 급등", "source": "서울신문"},
                ],
            },
            "collection": {"status": "ready"},
        },
    )

    payload = news.get_coin_news("ARBUSDT")

    assert payload["items"][0]["title"] == korean
    assert payload["items"][0]["original_title"] == english
    assert payload["items"][1]["title"] == "아비트럼 24시간 28% 급등"
    assert "original_title" not in payload["items"][1]


def test_title_translation_batches_only_untranslated_titles_and_reuses_cache(monkeypatch):
    calls = []
    english = "MUBARAK jumps as BNB Chain meme rally broadens"
    korean = "BNB 체인 밈코인 강세에 무바라크 급등"
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "test-key")
    monkeypatch.setattr(news, "_title_translation_cache", {}, raising=False)
    monkeypatch.setattr(news, "_title_translation_budget", ("", 0), raising=False)

    def translate(titles):
        calls.append(titles)
        return {english: korean}

    monkeypatch.setattr(
        news,
        "_request_korean_title_translations",
        translate,
        raising=False,
    )
    localize = getattr(news, "_localize_coin_news_items", lambda items: items)
    items = [
        {"title": english, "source": "Crypto News"},
        {"title": "무바라크 관련 국내 뉴스", "source": "국내 매체"},
    ]

    first = localize(items)
    second = localize(items)

    assert calls == [[english]]
    assert first[0]["title"] == korean
    assert second[0]["title"] == korean
    assert items[0]["title"] == english


def test_title_translation_retries_only_items_missing_from_a_partial_batch(monkeypatch):
    first = "Arbitrum token soars"
    second = "MUBARAK jumps as BNB Chain meme rally broadens"
    calls = []
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "test-key")
    monkeypatch.setattr(news, "_title_translation_cache", {})

    def translate(titles):
        calls.append(titles)
        if titles == [first, second]:
            return {first: "아비트럼 토큰 급등"}
        if titles == [second]:
            return {second: "BNB 체인 밈 랠리에 MUBARAK 급등"}
        return {}

    monkeypatch.setattr(news, "_request_korean_title_translations", translate)

    localized = news._localize_coin_news_items([
        {"title": first},
        {"title": second},
    ])

    assert calls == [[first, second], [second]]
    assert [item["title"] for item in localized] == [
        "아비트럼 토큰 급등",
        "BNB 체인 밈 랠리에 MUBARAK 급등",
    ]


def test_title_translation_applies_after_normalizing_source_whitespace(monkeypatch):
    raw = "Arbitrum   token\nsoars"
    normalized = "Arbitrum token soars"
    korean = "아비트럼 토큰 급등"
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "test-key")
    monkeypatch.setattr(news, "_title_translation_cache", {normalized: korean})

    localized = news._localize_coin_news_items([{"title": raw}])

    assert localized == [{"title": korean, "original_title": normalized}]


def test_title_translation_without_api_key_returns_originals(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", raising=False)
    monkeypatch.setattr(
        news,
        "_request_korean_title_translations",
        lambda _titles: (_ for _ in ()).throw(AssertionError("AI must not run")),
        raising=False,
    )
    localize = getattr(news, "_localize_coin_news_items", lambda items: items)
    item = {"title": "Arbitrum token rises", "source": "CoinDesk"}

    assert localize([item]) == [item]


def test_title_translation_parser_accepts_fenced_json_and_stable_ids():
    parser = getattr(news, "_parse_korean_title_translations", lambda *_args: {})
    title = "Arbitrum token soars"
    title_id = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    translated = parser(
        f'```json\n{{"items":[{{"id":"{title_id}",'
        '"title_ko":"아비트럼 토큰 급등"}]}\n```',
        [title],
    )

    assert translated == {title: "아비트럼 토큰 급등"}


@pytest.mark.parametrize(
    "translated_title",
    [
        "BTC 가격 $80,000 근처, ARB 28% 급등",
        "비트코인 가격 $78,000 근처, OP 28% 급등",
    ],
)
def test_title_translation_parser_rejects_changed_numbers_or_tickers(
    translated_title,
):
    original = "BTC price near $78,000 as ARB jumps 28%"
    title_id = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    payload = (
        '{"items":[{"id":"'
        + title_id
        + '","title_ko":"'
        + translated_title
        + '"}]}'
    )

    assert news._parse_korean_title_translations(payload, [original]) == {}


@pytest.mark.parametrize("translated_title", ["BTC 3% 상승", "BTC +3% 상승"])
def test_title_translation_parser_rejects_lost_or_reversed_numeric_sign(
    translated_title,
):
    original = "BTC drops -3%"
    title_id = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    payload = (
        '{"items":[{"id":"'
        + title_id
        + '","title_ko":"'
        + translated_title
        + '"}]}'
    )

    assert news._parse_korean_title_translations(payload, [original]) == {}


def test_title_translation_parser_accepts_equivalent_compact_usd_amount():
    original = "Trader nets $521K profit"
    title_id = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    payload = (
        '{"items":[{"id":"'
        + title_id
        + '","title_ko":"트레이더가 521,000달러 수익 달성"}]}'
    )

    assert news._parse_korean_title_translations(payload, [original]) == {
        original: "트레이더가 521,000달러 수익 달성",
    }


def test_title_translation_parser_accepts_equivalent_usd_code_and_word():
    original = "BTC nears USD 78,000"
    title_id = hashlib.sha256(original.encode("utf-8")).hexdigest()[:16]
    payload = (
        '{"items":[{"id":"'
        + title_id
        + '","title_ko":"BTC 78,000달러 근접"}]}'
    )

    assert news._parse_korean_title_translations(payload, [original]) == {
        original: "BTC 78,000달러 근접",
    }


def test_title_translation_daily_budget_caps_each_process(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "translation-key")
    monkeypatch.setattr(news, "_TITLE_TRANSLATION_MAX_CALLS_PER_DAY", 2)
    monkeypatch.setattr(news, "_title_translation_budget", ("", 0))
    monkeypatch.setattr(news, "_kst_date", lambda: "2026-09-03")

    assert news._reserve_title_translation_call() is True
    assert news._reserve_title_translation_call() is True
    assert news._reserve_title_translation_call() is False
    assert news._title_translation_budget == ("2026-09-03", 2)


def test_title_translation_budget_uses_durable_namespace_with_postgres(monkeypatch):
    calls = []
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "translation-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")

    def reserve(*, daily_limit):
        calls.append(daily_limit)
        return True

    monkeypatch.setattr(
        news,
        "_reserve_durable_title_translation_budget",
        reserve,
        raising=False,
    )

    assert news._reserve_title_translation_call() is True
    assert calls == [news._TITLE_TRANSLATION_MAX_CALLS_PER_DAY]


def test_title_translation_uses_only_the_dedicated_api_key(monkeypatch):
    captured = []
    requests = []
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "translation-key")

    class Block:
        type = "text"
        title = "Arbitrum token soars"
        title_id = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
        text = (
            '{"items":[{"id":"'
            + title_id
            + '","title_ko":"아비트럼 토큰 급등"}]}'
        )

    class Messages:
        def create(self, **kwargs):
            requests.append(kwargs)
            return type("Response", (), {"content": [Block()]})()

    class Client:
        messages = Messages()

    def fake_client(**kwargs):
        captured.append(kwargs)
        return Client()

    class Runtime:
        def call(self, _key, loader, **_kwargs):
            return loader(), "loaded"

    monkeypatch.setattr(news, "get_anthropic_client", fake_client)
    monkeypatch.setattr(news, "get_ai_runtime", lambda: Runtime())

    translated = news._request_korean_title_translations(
        ["Arbitrum token soars"],
    )

    assert translated == {"Arbitrum token soars": "아비트럼 토큰 급등"}
    assert captured == [{"api_key": "translation-key"}]
    assert "표기를 원문 문자열 그대로" in requests[0]["system"]


def test_title_translation_budget_is_charged_only_for_actual_api_load(monkeypatch):
    title = "Arbitrum token soars"
    title_id = hashlib.sha256(title.encode("utf-8")).hexdigest()[:16]
    monkeypatch.setenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", "translation-key")
    monkeypatch.setattr(news, "_title_translation_budget", ("", 0))
    monkeypatch.setattr(news, "_kst_date", lambda: "2026-09-03")
    api_calls = []

    class Block:
        type = "text"
        text = (
            '{"items":[{"id":"'
            + title_id
            + '","title_ko":"아비트럼 토큰 급등"}]}'
        )

    class Messages:
        def create(self, **_kwargs):
            api_calls.append(True)
            return type("Response", (), {"content": [Block()]})()

    class Runtime:
        cached = None

        def call(self, _key, loader, **_kwargs):
            if self.cached is None:
                self.cached = loader()
                return self.cached, "loaded"
            return self.cached, "cached"

    runtime = Runtime()
    monkeypatch.setattr(
        news,
        "get_anthropic_client",
        lambda **_kwargs: type("Client", (), {"messages": Messages()})(),
    )
    monkeypatch.setattr(news, "get_ai_runtime", lambda: runtime)

    assert news._request_korean_title_translations([title])
    assert news._request_korean_title_translations([title])
    assert len(api_calls) == 1
    assert news._title_translation_budget == ("2026-09-03", 1)


def test_coin_news_reuses_active_snapshot_outside_fixed_universe(monkeypatch):
    news._coin_cache.clear()
    item = {
        "title": "활성 수집기가 저장한 버블맵스 소식",
        "source": "테스트 매체",
        "url": "https://news.example.com/bmt",
        "published": "2026-08-24T04:00:00+00:00",
        "published_display": "10분 전",
    }
    loaded = []

    def fake_load(symbol):
        loaded.append(symbol)
        return {
            "snapshot_id": "bmt-shared-snapshot",
            "news_payload": {"symbol": "BMT", "items": [item]},
            "collection": {"status": "ready"},
        }

    def unexpected_rss(*_args, **_kwargs):
        raise AssertionError("활성 수집 스냅샷이 있으면 RSS를 다시 호출하면 안 됩니다")

    monkeypatch.setattr(news, "_load_latest_coin_snapshot", fake_load)
    monkeypatch.setattr(news, "_fetch_news", unexpected_rss)

    payload = news.get_coin_news("BMTUSDT")

    assert loaded == ["BMT"]
    assert payload["symbol"] == "BMT"
    assert payload["items"] == [item]
    assert payload["data_source"] == "prefect_db"
    assert payload["snapshot_id"] == "bmt-shared-snapshot"


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
    assert [entry["title"] for entry in payload["items"]] == [item["title"]]
    assert payload["items"][0]["feed_source"] == "google_news_rss"
    assert payload["data_source"] == "rss_cache"


def test_public_coin_news_fallback_filters_unrelated_broad_search_results(monkeypatch):
    news._coin_cache.clear()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_NEWS_TRANSLATION_API_KEY", raising=False)
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _symbol: None)

    def fake_fetch(_query, **_kwargs):
        return [
            {
                "title": "DeepOceanCrypto creator insights",
                "source": "Binance",
                "url": "https://example.com/unrelated",
            },
            {
                "title": "MUBARAK jumps 26% as BNB Chain meme rally broadens",
                "source": "Crypto News",
                "url": "https://example.com/mubarak",
            },
            {
                "title": "Chump Coin price today and market cap",
                "source": "CoinMarketCap",
                "url": "https://example.com/chump",
            },
        ]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)

    payload = news.get_coin_news("MUBARAKUSDT")

    assert [item["title"] for item in payload["items"]] == [
        "MUBARAK jumps 26% as BNB Chain meme rally broadens",
    ]


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
        ("TUSDT", "T"),
        ("A", "A"),
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


def test_single_letter_asset_searches_current_and_five_year_archive(monkeypatch):
    calls = []

    def fake_fetch(query, *, limit, strict, locale):
        calls.append((query, locale))
        if "when:5y" not in query:
            return []
        return [{
            "title": "Threshold Network expands tBTC infrastructure",
            "source": "Archive News",
            "url": "https://news.example.com/threshold-2025",
            "published": "2025-09-25T07:00:00+00:00",
            "published_display": "",
        }]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)

    payload = news._coin_news_envelope("T", strict=True, relevant_only=True)

    assert payload["coin_name"] == "쓰레스홀드"
    assert [item["title"] for item in payload["items"]] == [
        "Threshold Network expands tBTC infrastructure",
    ]
    assert any("when:30d" in query for query, _locale in calls)
    assert any("when:5y" in query for query, _locale in calls)


def test_unknown_active_asset_falls_back_to_five_year_search(monkeypatch):
    calls = []

    def fake_fetch(query, *, limit, strict, locale):
        calls.append((query, locale))
        if "when:5y" not in query:
            return []
        return [{
            "title": "XYZ token launched its storage network in 2025",
            "source": "Archive News",
            "url": "https://news.example.com/xyz-2025",
            "published": "2025-04-21T07:00:00+00:00",
            "published_display": "",
        }]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)

    payload = news._coin_news_envelope("XYZ", strict=True, relevant_only=True)

    assert [item["title"] for item in payload["items"]] == [
        "XYZ token launched its storage network in 2025",
    ]
    assert any("when:30d" in query for query, _locale in calls)
    assert any("when:5y" in query for query, _locale in calls)


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
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)
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
        "오픈에덴, 토큰화 국채 상품 확대",
        "OpenEden expands its tokenized Treasury platform",
    ]
    assert payload["items"][1]["source"] == "CoinDesk"
    assert payload["items"][1]["categories"] == ["Finance", "Tokenization"]
    assert payload["items"][1]["feed_source"] == "coindesk_rss"
    assert payload["items"][0]["feed_source"] == "google_news_rss"
    source_states = {
        source["name"]: (source["status"], source["item_count"])
        for source in payload["sources"]
    }
    assert {
        name: source_states[name]
        for name in {
            "openeden_official_rss",
            "coindesk_rss",
            "google_news_rss",
        }
    } == {
        "openeden_official_rss": ("ready", 0),
        "coindesk_rss": ("ready", 1),
        "google_news_rss": ("ready", 1),
    }
    assert {
        name for name in source_states if name.startswith("coindesk_section_")
    } == {
        "coindesk_section_markets",
        "coindesk_section_policy",
        "coindesk_section_tech",
        "coindesk_section_business",
    }
    assert {
        name for name in source_states if name.startswith("coindesk_topic_")
    } == {
        "coindesk_topic_bitcoin",
        "coindesk_topic_ethereum",
        "coindesk_topic_ripple",
        "coindesk_topic_solana",
    }
    assert all(
        state == ("ready", 0)
        for name, state in source_states.items()
        if name.startswith(("coindesk_section_", "coindesk_topic_"))
    )
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
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_discovery_news",
        _empty_coindesk_discovery,
    )
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
        market["title"],
        ticker_market["title"],
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


def test_connected_bmt_uses_project_alias_and_broad_english_queries(monkeypatch):
    korean = {
        "title": "업비트, 버블맵스(BMT) USDT 마켓 상장",
        "source": "이코노미블록",
        "url": "https://news.example.com/bmt-ko",
    }
    english = {
        "title": "What Is Bubblemaps (BMT) and What On-Chain Forensics Shows",
        "source": "Phemex",
        "url": "https://news.example.com/bmt-en",
    }
    calls = []

    def fake_fetch(query, *, limit, strict, locale="ko"):
        calls.append((query, limit, strict, locale))
        return [korean] if locale == "ko" else [english]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)

    payload = news._coin_news_envelope(
        "BMT",
        strict=True,
        relevant_only=True,
    )

    assert "BMT" not in news.position_news_collection_universe()
    assert [item["title"] for item in payload["items"]] == [
        korean["title"],
        english["title"],
    ]
    assert calls == [
        (
            '(BMT 코인 OR BMT 토큰 OR 버블맵스) when:30d',
            50,
            True,
            "ko",
        ),
        (
            '(BMT coin OR BMT crypto OR BMT token OR $BMT OR BMT USDT OR '
            'Bubblemaps) when:30d',
            50,
            True,
            "en",
        ),
    ]


def test_siacoin_uses_project_name_instead_of_bare_sc_queries(monkeypatch):
    calls = []

    def fake_fetch(query, *, limit, strict, locale="ko"):
        calls.append((query, limit, strict, locale))
        return []

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)

    payload = news._coin_news_envelope(
        "SC",
        strict=True,
        relevant_only=True,
    )

    assert payload["coin_name"] == "시아코인"
    assert calls == [
        (
            '(시아코인 OR "SC 코인" OR SCUSDT) when:30d',
            50,
            True,
            "ko",
        ),
        (
            '(Siacoin OR "Sia coin" OR "Sia network" OR '
            '"Sia blockchain" OR SCUSDT) when:30d',
            50,
            True,
            "en",
        ),
    ]


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
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_discovery_news",
        _empty_coindesk_discovery,
    )
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
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_discovery_news",
        _empty_coindesk_discovery,
    )
    monkeypatch.setattr(news.httpx, "Client", lambda **_kwargs: FakeClient())

    bitcoin = news.fetch_coin_news_for_collector("BTC")
    ethereum = news.fetch_coin_news_for_collector("ETH")

    assert [item["title"] for item in bitcoin["items"]] == [
        "Strategy raises fresh cash through MSTR sales",
    ]
    assert [item["title"] for item in ethereum["items"]] == [
        "Ethereum upgrade changes wallet gas assumptions",
    ]
    assert bitcoin["items"][0]["excerpt"] == (
        "Strategy raised new capital by selling shares of MSTR."
    )
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


def test_final_news_list_is_sorted_newest_first_with_undated_items_last():
    unsorted = [
        {"title": "2023 article", "published": "2023-05-11T13:10:00+00:00"},
        {"title": "undated article", "published": None},
        {"title": "2026 article", "published": "2026-09-02T22:12:43+00:00"},
        {"title": "2025 article", "published": "2025-07-07T15:00:00+00:00"},
    ]
    sorter = getattr(news, "_sort_news_items_newest_first", lambda items: items)

    sorted_items = sorter(unsorted)

    assert [item["title"] for item in sorted_items] == [
        "2026 article",
        "2025 article",
        "2023 article",
        "undated article",
    ]


def test_coindesk_playwright_links_keep_only_dated_articles():
    raw_links = [
        {
            "href": (
                "https://www.coindesk.com/markets/2026/09/01/"
                "bitcoin-steady?utm_source=homepage"
            ),
            "title": "  Bitcoin steady above $78,000   as markets recover  ",
            "published": "2026-09-01T04:00:00Z",
            "excerpt": " Bitcoin held its range while broader markets recovered. ",
        },
        {
            "href": "https://www.coindesk.com/markets",
            "title": "Markets",
            "published": "",
        },
        {
            "href": "https://example.com/markets/2026/09/01/not-coindesk",
            "title": "Not CoinDesk",
            "published": "",
        },
        {
            "href": "https://www.coindesk.com/markets/2026/09/01/bitcoin-steady",
            "title": "A second card for the same Bitcoin article",
            "published": "2026-09-01T04:00:00Z",
        },
    ]

    items = news._parse_coindesk_browser_links(
        raw_links,
        source_kind="section",
        source_scope="markets",
        source_page="https://www.coindesk.com/markets",
    )

    assert items == [
        {
            "title": "Bitcoin steady above $78,000 as markets recover",
            "source": "CoinDesk",
            "url": (
                "https://www.coindesk.com/markets/2026/09/01/bitcoin-steady"
            ),
            "published": "2026-09-01T04:00:00+00:00",
            "published_display": "",
            "excerpt": "Bitcoin held its range while broader markets recovered.",
            "feed_source": "coindesk_section_playwright",
            "source_scope": "markets",
            "source_page": "https://www.coindesk.com/markets",
        }
    ]


def test_coindesk_search_link_uses_article_path_date_when_card_has_no_time():
    items = news._parse_coindesk_browser_links(
        [{
            "href": (
                "https://www.coindesk.com/markets/2025/07/07/"
                "threshold-tbtc-debuts-on-sui"
            ),
            "title": "Threshold's Bitcoin Backed tBTC Debuts on Sui",
            "published": "",
        }],
        source_kind="asset_search",
        source_scope="T",
        source_page="https://www.coindesk.com/search/",
    )

    assert items[0]["published"] == "2025-07-07T00:00:00+00:00"


def test_coindesk_asset_archive_uses_project_aliases_and_filters_noise(monkeypatch):
    searched = []

    def fake_search(terms):
        searched.extend(terms)
        return [
            {
                "title": "Threshold Network Goes Live With Wormhole",
                "source": "CoinDesk",
                "url": "https://www.coindesk.com/tech/2023/05/11/threshold-wormhole",
                "published": "2023-05-11T00:00:00+00:00",
            },
            {
                "title": "Threshold's Bitcoin Backed tBTC Debuts on Sui",
                "source": "CoinDesk",
                "url": "https://www.coindesk.com/markets/2025/07/07/threshold-tbtc",
                "published": "2025-07-07T00:00:00+00:00",
            },
            {
                "title": "Sentinel Network Reports HitBTC Breach",
                "source": "CoinDesk",
                "url": "https://www.coindesk.com/markets/2021/08/20/hitbtc-breach",
                "published": "2021-08-20T00:00:00+00:00",
            },
        ]

    monkeypatch.setattr(
        news,
        "_search_coindesk_asset_archive_playwright",
        fake_search,
        raising=False,
    )
    loader = getattr(news, "_load_coindesk_asset_archive", lambda *_args: [])

    items = loader("T", "쓰레스홀드")

    assert searched == ["tbtc", "threshold network"]
    assert [item["title"] for item in items] == [
        "Threshold Network Goes Live With Wormhole",
        "Threshold's Bitcoin Backed tBTC Debuts on Sui",
    ]


def test_coindesk_search_wait_passes_token_as_keyword_only_argument():
    calls = []

    class FakePage:
        def wait_for_function(self, expression, *, arg, timeout):
            calls.append((expression, arg, timeout))

    wait = getattr(news, "_wait_for_coindesk_asset_results", lambda *_args: None)
    wait(FakePage(), "tbtc", 12_000)

    assert len(calls) == 1
    assert calls[0][1:] == ("tbtc", 12_000)


def test_coindesk_search_wait_timeout_allows_current_dom_to_be_parsed():
    class SlowPage:
        def wait_for_function(self, _expression, *, arg, timeout):
            raise RuntimeError(f"timeout for {arg} after {timeout}")

    assert news._wait_for_coindesk_asset_results(
        SlowPage(),
        "tbtc",
        12_000,
    ) is False


def test_empty_coindesk_archive_uses_short_retry_cache_window():
    ttl = getattr(
        news,
        "_coindesk_asset_archive_cache_seconds",
        lambda _items: news._COINDESK_DISCOVERY_MAX_STALE_SECONDS,
    )

    assert ttl([]) == news._COIN_CACHE_SECONDS
    assert ttl([{"title": "Threshold Network archive article"}]) == (
        news._COINDESK_DISCOVERY_MAX_STALE_SECONDS
    )


def test_collector_uses_asset_archive_when_current_coindesk_has_no_match(monkeypatch):
    archive_calls = []
    archive_item = {
        "title": "Threshold's Bitcoin Backed tBTC Debuts on Sui",
        "source": "CoinDesk",
        "url": "https://www.coindesk.com/markets/2025/07/07/threshold-tbtc",
        "published": "2025-07-07T00:00:00+00:00",
        "feed_source": "coindesk_asset_search_playwright",
    }
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_discovery_news",
        _empty_coindesk_discovery,
    )
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_kwargs: [])
    monkeypatch.setattr(
        news,
        "_coin_news_envelope",
        lambda *_args, **_kwargs: {
            "symbol": "T",
            "coin_name": "쓰레스홀드",
            "items": [],
            "candidate_count": 0,
            "query": "",
        },
    )
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_asset_archive_news",
        lambda asset, coin_name, **_kwargs: (
            archive_calls.append((asset, coin_name)) or [archive_item]
        ),
    )

    payload = news.fetch_coin_news_for_collector("T")

    assert archive_calls == [("T", "쓰레스홀드")]
    assert payload["items"] == [archive_item]
    archive_source = next(
        source for source in payload["sources"]
        if source["name"] == "coindesk_asset_archive"
    )
    assert archive_source["status"] == "ready"
    assert archive_source["item_count"] == 1


def test_collector_merges_archive_even_when_current_coindesk_has_a_match(monkeypatch):
    current = {
        "title": "Threshold Network announces a Bitcoin integration",
        "source": "CoinDesk",
        "url": "https://www.coindesk.com/tech/2026/09/01/threshold-current",
        "published": "2026-09-01T00:00:00+00:00",
    }
    archived = {
        "title": "Threshold's Bitcoin Backed tBTC Debuts on Sui",
        "source": "CoinDesk",
        "url": "https://www.coindesk.com/markets/2025/07/07/threshold-tbtc",
        "published": "2025-07-07T00:00:00+00:00",
    }
    archive_calls = []
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_discovery_news",
        _empty_coindesk_discovery,
    )
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_kwargs: [current])
    monkeypatch.setattr(
        news,
        "_coin_news_envelope",
        lambda *_args, **_kwargs: {"items": [], "candidate_count": 0, "query": ""},
    )
    monkeypatch.setattr(
        news,
        "_fetch_coindesk_asset_archive_news",
        lambda asset, coin_name, **_kwargs: (
            archive_calls.append((asset, coin_name)) or [archived]
        ),
    )

    payload = news.fetch_coin_news_for_collector("T")

    assert archive_calls == [("T", "쓰레스홀드")]
    assert [item["title"] for item in payload["items"]] == [
        current["title"],
        archived["title"],
    ]
    assert payload["items"][0]["feed_source"] == "coindesk_rss"


def test_article_excerpt_enrichment_reuses_feed_text_and_fetches_only_missing(monkeypatch):
    requested = []
    items = [
        {
            "title": "CoinDesk article",
            "url": "https://www.coindesk.com/markets/2026/09/01/article",
            "excerpt": "  Feed-provided   article summary. ",
        },
        {
            "title": "Publisher article",
            "url": "https://news.google.com/rss/articles/redirect",
        },
        {
            "title": "Fourth article",
            "url": "https://example.com/fourth",
        },
    ]

    def fake_fetch(targets):
        requested.extend(targets)
        return ["Publisher page body with the relevant facts."]

    monkeypatch.setattr(news, "_fetch_article_excerpts_playwright", fake_fetch)

    enriched = news.enrich_article_excerpts(items, limit=2)

    assert enriched[0]["excerpt"] == "Feed-provided article summary."
    assert enriched[1]["excerpt"] == "Publisher page body with the relevant facts."
    assert "excerpt" not in enriched[2]
    assert [item["title"] for item in requested] == ["Publisher article"]


def test_coindesk_discovery_prefers_playwright_pages(monkeypatch):
    browser_calls = []

    def browser_fetch(descriptors):
        browser_calls.append(tuple(descriptors))
        return {
            name: [
                {
                    "title": f"Browser headline for {scope}",
                    "source": "CoinDesk",
                    "url": (
                        f"https://www.coindesk.com/markets/2026/09/01/{scope}"
                    ),
                    "published": "2026-09-01T04:00:00+00:00",
                    "published_display": "1시간 전",
                    "feed_source": f"coindesk_{kind}_playwright",
                    "source_scope": scope,
                    "source_page": page,
                }
            ]
            for name, kind, scope, _query, page in descriptors
        }

    monkeypatch.setattr(
        news,
        "_fetch_coindesk_pages_playwright",
        browser_fetch,
        raising=False,
    )
    monkeypatch.setattr(
        news,
        "_fetch_news",
        lambda *_args, **_kwargs: pytest.fail(
            "Google RSS fallback must not run for a ready browser page"
        ),
    )
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    payload = news._fetch_coindesk_discovery_news(strict=True)

    assert len(browser_calls) == 1
    assert len(payload["items"]) == len(news._COINDESK_DISCOVERY_SOURCES)
    assert all(source["status"] == "ready" for source in payload["sources"])
    assert {
        source["source_type"] for source in payload["sources"]
    } == {
        "coindesk_section_playwright",
        "coindesk_topic_playwright",
    }


def test_coindesk_discovery_fetches_requested_sections_and_topics_once(monkeypatch):
    calls = []

    def fake_fetch(query, *, limit, strict, locale):
        calls.append((query, limit, strict, locale))
        return [
            {
                "title": f"CoinDesk result for {query}",
                "source": "CoinDesk",
                "url": f"https://news.google.com/rss/articles/{len(calls)}",
                "published": "2026-09-01T00:00:00+00:00",
                "published_display": "1시간 전",
            },
            {
                "title": "Unexpected publisher",
                "source": "Other News",
                "url": "https://news.google.com/rss/articles/other",
                "published": "2026-09-01T00:00:00+00:00",
                "published_display": "1시간 전",
            },
        ]

    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    first = news._fetch_coindesk_discovery_news(strict=True)
    second = news._fetch_coindesk_discovery_news(strict=True)

    expected_names = {
        "coindesk_section_markets",
        "coindesk_section_policy",
        "coindesk_section_tech",
        "coindesk_section_business",
        "coindesk_topic_bitcoin",
        "coindesk_topic_ethereum",
        "coindesk_topic_ripple",
        "coindesk_topic_solana",
    }
    assert {source["name"] for source in first["sources"]} == expected_names
    assert all(source["status"] == "ready" for source in first["sources"])
    assert all(source["fetched_count"] == 1 for source in first["sources"])
    assert len(first["items"]) == 8
    assert first == second
    assert len(calls) == 8
    assert all(
        limit == 50 and strict and locale == "en"
        for _, limit, strict, locale in calls
    )
    assert all(item["source"] == "CoinDesk" for item in first["items"])
    assert {
        item["feed_source"] for item in first["items"]
    } == {
        "coindesk_section_google_rss",
        "coindesk_topic_google_rss",
    }


def test_coindesk_discovery_reuses_stale_bundle_after_refresh_failure(monkeypatch):
    now = [100.0]
    calls = []
    failing = [False]

    def fake_fetch(query, **_kwargs):
        calls.append(query)
        if failing[0]:
            raise news.NewsFetchError("upstream unavailable")
        return [{"title": f"CoinDesk {query}", "source": "CoinDesk"}]

    monkeypatch.setattr(news.time, "time", lambda: now[0])
    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    fresh = news._fetch_coindesk_discovery_news(strict=True)
    failing[0] = True
    now[0] += news._COIN_CACHE_SECONDS + 1
    stale = news._fetch_coindesk_discovery_news(strict=True)

    assert stale["items"] == fresh["items"]
    assert all(source["status"] == "stale" for source in stale["sources"])
    assert len(calls) == 16


def test_coindesk_discovery_failure_cache_last_for_full_collection_cycle(monkeypatch):
    now = [100.0]
    calls = []

    def fail(query, **_kwargs):
        calls.append(query)
        raise news.NewsFetchError("upstream unavailable")

    monkeypatch.setattr(news.time, "time", lambda: now[0])
    monkeypatch.setattr(news, "_fetch_news", fail)
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    with pytest.raises(news.NewsFetchError):
        news._fetch_coindesk_discovery_news(strict=True)
    now[0] += 61
    with pytest.raises(news.NewsFetchError):
        news._fetch_coindesk_discovery_news(strict=True)

    assert len(calls) == 8


def test_coindesk_discovery_keeps_only_failed_source_stale(monkeypatch):
    now = [100.0]
    refresh = [False]
    markets_query = "site:coindesk.com/markets when:30d"

    def fake_fetch(query, **_kwargs):
        if refresh[0] and query == markets_query:
            raise news.NewsFetchError("markets unavailable")
        prefix = "new" if refresh[0] else "old"
        return [{"title": f"{prefix} {query}", "source": "CoinDesk"}]

    monkeypatch.setattr(news.time, "time", lambda: now[0])
    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    fresh = news._fetch_coindesk_discovery_news(strict=True)
    old_markets = fresh["items_by_source"]["coindesk_section_markets"]
    refresh[0] = True
    now[0] += news._COIN_CACHE_SECONDS + 1
    partial = news._fetch_coindesk_discovery_news(strict=True)
    sources = {source["name"]: source for source in partial["sources"]}

    assert partial["items_by_source"]["coindesk_section_markets"] == old_markets
    assert sources["coindesk_section_markets"]["status"] == "stale"
    assert sources["coindesk_section_policy"]["status"] == "ready"
    assert partial["items_by_source"]["coindesk_section_policy"][0][
        "title"
    ].startswith("new ")


def test_coindesk_discovery_does_not_promote_never_ready_source_to_stale(monkeypatch):
    now = [100.0]
    markets_query = "site:coindesk.com/markets when:30d"

    def fake_fetch(query, **_kwargs):
        if query == markets_query:
            raise news.NewsFetchError("markets unavailable")
        return [{"title": query, "source": "CoinDesk"}]

    monkeypatch.setattr(news.time, "time", lambda: now[0])
    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    first = news._fetch_coindesk_discovery_news(strict=True)
    now[0] += news._COIN_CACHE_SECONDS + 1
    second = news._fetch_coindesk_discovery_news(strict=True)

    for payload in (first, second):
        source = next(
            item
            for item in payload["sources"]
            if item["name"] == "coindesk_section_markets"
        )
        assert source["status"] == "error"


def test_coindesk_discovery_stale_bundle_has_maximum_age(monkeypatch):
    now = [100.0]
    failing = [False]

    def fake_fetch(query, **_kwargs):
        if failing[0]:
            raise news.NewsFetchError("upstream unavailable")
        return [{"title": query, "source": "CoinDesk"}]

    monkeypatch.setattr(news.time, "time", lambda: now[0])
    monkeypatch.setattr(news, "_fetch_news", fake_fetch)
    monkeypatch.setattr(news, "_coindesk_discovery_cache", None, raising=False)
    monkeypatch.setattr(news, "_coindesk_discovery_error_cache", None, raising=False)

    news._fetch_coindesk_discovery_news(strict=True)
    failing[0] = True
    now[0] += news._COINDESK_DISCOVERY_MAX_STALE_SECONDS + 1

    with pytest.raises(news.NewsFetchError):
        news._fetch_coindesk_discovery_news(strict=True)


def test_collector_merges_relevant_coindesk_discovery_source(monkeypatch):
    xrp = {
        "title": "Ripple prepares XRP Ledger for quantum computers",
        "source": "CoinDesk",
        "url": "https://news.google.com/rss/articles/xrp",
        "published": "2026-08-29T05:48:04+00:00",
        "published_display": "3일 전",
        "feed_source": "coindesk_topic_google_rss",
        "source_scope": "ripple",
        "source_page": "https://www.coindesk.com/tag/ripple",
    }
    ethereum = {
        "title": "Ethereum developers prepare a new upgrade",
        "source": "CoinDesk",
        "url": "https://news.google.com/rss/articles/eth",
        "published": "2026-08-29T04:00:00+00:00",
        "published_display": "3일 전",
        "feed_source": "coindesk_section_google_rss",
        "source_scope": "tech",
        "source_page": "https://www.coindesk.com/tech",
    }
    discovery = {
        "items": [xrp, ethereum],
        "items_by_source": {
            "coindesk_topic_ripple": [xrp],
            "coindesk_section_tech": [ethereum],
        },
        "sources": [
            {
                "name": "coindesk_topic_ripple",
                "source_type": "coindesk_topic_google_rss",
                "source_page": "https://www.coindesk.com/tag/ripple",
                "status": "ready",
                "fetched_count": 1,
            },
            {
                "name": "coindesk_section_tech",
                "source_type": "coindesk_section_google_rss",
                "source_page": "https://www.coindesk.com/tech",
                "status": "ready",
                "fetched_count": 1,
            },
        ],
    }

    monkeypatch.setattr(
        news,
        "_fetch_coindesk_discovery_news",
        lambda **_kwargs: discovery,
    )
    monkeypatch.setattr(
        news,
        "_coin_news_envelope",
        lambda *_args, **_kwargs: {"items": [], "candidate_count": 0, "query": "XRP"},
    )
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_kwargs: [])

    payload = news.fetch_coin_news_for_collector("XRP")

    assert payload["items"] == [xrp]
    sources = {source["name"]: source for source in payload["sources"]}
    assert sources["coindesk_topic_ripple"]["item_count"] == 1
    assert sources["coindesk_section_tech"]["item_count"] == 0


def test_all_supported_assets_have_english_news_aliases():
    assert set(news._COIN_KO) <= set(news._COIN_ALIASES)


@pytest.mark.parametrize(
    ("symbol", "coin_name", "title"),
    [
        ("AVAX", "아발란체", "Avalanche launches a new subnet"),
        ("LINK", "체인링크", "Chainlink expands its data platform"),
        ("ADA", "에이다", "Cardano developers publish a roadmap"),
        ("DOT", "폴카닷", "Polkadot governance approves an upgrade"),
    ],
)
def test_asset_matcher_recognizes_supported_english_coin_names(
    symbol,
    coin_name,
    title,
):
    assert news._matches_asset(
        {"title": title, "categories": []},
        symbol,
        coin_name,
    )


@pytest.mark.parametrize(
    ("symbol", "coin_name", "title"),
    [
        ("NEAR", "니어프로토콜", "Bitcoin trades near a record high"),
        ("NEAR", "니어프로토콜", "Bitcoin near a record high"),
        ("LINK", "체인링크", "The link between crypto and inflation"),
        ("LINK", "체인링크", "Bitcoin link to inflation data"),
        ("OP", "옵티미즘", "CoinDesk op-ed examines regulation"),
        ("OP", "옵티미즘", "Bitcoin op-ed examines regulation"),
        ("SAND", "샌드박스", "Traders draw a line in the sand"),
        ("ETC", "이더리움클래식", "Bitcoin, ether, etc. rally together"),
        ("ETC", "이더리움클래식", "Bitcoin etc. rally together"),
    ],
)
def test_asset_matcher_rejects_ambiguous_lowercase_ticker_words(
    symbol,
    coin_name,
    title,
):
    assert not news._matches_asset(
        {"title": title, "categories": []},
        symbol,
        coin_name,
    )


@pytest.mark.parametrize(
    "title",
    [
        "LINK rallies after a new oracle partnership",
        "The link token gains exchange support",
        "$LINK liquidity rises",
    ],
)
def test_asset_matcher_accepts_explicit_ambiguous_ticker_context(title):
    assert news._matches_asset(
        {"title": title, "categories": []},
        "LINK",
        "체인링크",
    )


@pytest.mark.parametrize(
    "title",
    [
        "SC제일·씨티 외국계銀 2분기 순익 ‘껑충’⋯비이자이익이 견인",
        "알테오젠, 키트루다SC 판매 마일스톤 2500만달러 첫 수령",
        "Stake.us Promo Code: COVERSBONUS for $55 Free SC + 550K GC",
        "PlayFame bonus: Get 500K GC, 250 SC & 250 Free Spins",
    ],
)
def test_siacoin_matcher_rejects_unqualified_sc_abbreviation(title):
    assert not news._matches_asset(
        {"title": title, "categories": []},
        "SC",
        "시아코인",
    )


@pytest.mark.parametrize(
    "title",
    [
        "Siacoin storage network ships a protocol upgrade",
        "Sia network adds a new storage provider",
        "SC coin rallies after an exchange listing",
        "$SC token volume rises after the upgrade",
        "SCUSDT futures volume reaches a monthly high",
    ],
)
def test_siacoin_matcher_accepts_project_or_crypto_context(title):
    assert news._matches_asset(
        {"title": title, "categories": []},
        "SC",
        "시아코인",
    )


@pytest.mark.parametrize(
    "title",
    [
        "Naser Taher receives award from Sheikh Nahyan bin Mubarak Al Nahyan",
        "Eid Adha Mubarak: Celebrate Eid and share 65,000 USDT in Eidiya",
    ],
)
def test_mubarak_matcher_rejects_person_name_and_greeting_noise(title):
    assert not news._matches_asset(
        {"title": title, "categories": []},
        "MUBARAK",
        "MUBARAK",
    )


@pytest.mark.parametrize(
    "title",
    [
        "MUBARAK jumps 26% as BNB Chain meme rally broadens",
        "Mubarak jumps 26% as BNB Chain meme rally broadens",
        "Binance will list Mubarak (MUBARAK) with Seed Tag",
        "What Is Mubarak Meme Coin?",
    ],
)
def test_mubarak_matcher_accepts_explicit_token_context(title):
    assert news._matches_asset(
        {"title": title, "categories": []},
        "MUBARAK",
        "MUBARAK",
    )


@pytest.mark.parametrize(
    ("symbol", "coin_name", "title"),
    [
        ("GRT", "더그래프", "The Graph launches a new data service"),
        ("STX", "스택스", "Stacks activates a network upgrade"),
        ("XLM", "스텔라루멘", "Stellar expands payments access"),
        ("MKR", "메이커", "Maker governance approves a proposal"),
        ("IMX", "이뮤터블", "Immutable launches a gaming chain"),
        ("SAND", "샌드박스", "Sandbox opens a creator program"),
    ],
)
def test_asset_matcher_recognizes_ambiguous_canonical_project_names(
    symbol,
    coin_name,
    title,
):
    assert news._matches_asset(
        {"title": title, "categories": []},
        symbol,
        coin_name,
    )


@pytest.mark.parametrize(
    ("symbol", "coin_name", "title"),
    [
        ("AVAX", "아발란체", "A liquidation avalanche hits crypto markets"),
        ("OP", "옵티미즘", "Bitcoin optimism rises as traders return"),
        ("GRT", "더그래프", "The graph shows Bitcoin volatility"),
        ("STX", "스택스", "Bitcoin stacks up another weekly gain"),
        ("XLM", "스텔라루멘", "Bitcoin posts stellar returns"),
        ("MKR", "메이커", "A market maker expands liquidity"),
        ("IMX", "이뮤터블", "Developers propose an immutable ledger"),
        ("SAND", "샌드박스", "Regulators open a crypto sandbox"),
    ],
)
def test_asset_matcher_rejects_generic_project_name_words(
    symbol,
    coin_name,
    title,
):
    assert not news._matches_asset(
        {"title": title, "categories": []},
        symbol,
        coin_name,
    )


@pytest.mark.parametrize(
    "title",
    [
        "Table Trac (TBTC) awards stock options to its CEO",
        "Threshold Network GARCH Model for time series analysis",
    ],
)
def test_threshold_matcher_rejects_stock_ticker_and_academic_noise(title):
    assert not news._matches_asset(
        {"title": title, "categories": []},
        "T",
        "쓰레스홀드",
    )


@pytest.mark.parametrize(
    "title",
    [
        "Threshold Network expands Bitcoin-backed tBTC to Sui",
        "Bitcoin-on-Ethereum Token tBTC relaunches",
        "쓰레스홀드 네트워크, tBTC 앱 업그레이드 공개",
    ],
)
def test_threshold_matcher_keeps_project_specific_news(title):
    assert news._matches_asset(
        {"title": title, "categories": []},
        "T",
        "쓰레스홀드",
    )


@pytest.mark.parametrize(
    ("symbol", "coin_name", "title"),
    [
        ("OP", "옵티미즘", "Optimism over bitcoin ETF approval grows"),
        ("STX", "스택스", "Stacks of cash move into bitcoin ETFs"),
        ("XLM", "스텔라루멘", "Stellar quarter for bitcoin miners"),
        ("SAND", "샌드박스", "Sandbox testing becomes mandatory under EU rules"),
    ],
)
def test_asset_matcher_rejects_title_case_generic_project_words(
    symbol,
    coin_name,
    title,
):
    assert not news._matches_asset(
        {"title": title, "categories": []},
        symbol,
        coin_name,
    )


@pytest.mark.parametrize(
    ("symbol", "coin_name", "title"),
    [
        ("OP", "옵티미즘", "Optimism grows its developer ecosystem"),
        ("XLM", "스텔라루멘", "Stellar gains institutional adoption"),
        ("MKR", "메이커", "Maker of DAI proposes a governance change"),
        ("GRT", "더그래프", "The Graph shows stronger indexing demand"),
    ],
)
def test_asset_matcher_keeps_project_names_with_asset_context(
    symbol,
    coin_name,
    title,
):
    assert news._matches_asset(
        {"title": title, "categories": []},
        symbol,
        coin_name,
    )


@pytest.mark.parametrize(
    "document",
    [
        "<html><body>security checkpoint</body></html>",
        '<feed xmlns="http://www.w3.org/2005/Atom"></feed>',
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"></rdf:RDF>',
    ],
)
def test_strict_google_feed_rejects_non_rss_checkpoint(monkeypatch, document):
    class Response:
        text = document

        @staticmethod
        def raise_for_status():
            return None

    class Client:
        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    monkeypatch.setattr(news, "get_http_client", lambda: Client())

    with pytest.raises(news.NewsFetchError):
        news._fetch_news(
            "unique-malformed-feed-query",
            strict=True,
            locale="en",
        )


@pytest.mark.parametrize(
    "symbol",
    [
        "A",
        "T",
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


@pytest.mark.parametrize("symbol", ["A" * 16, "../BTC", "BTC-USDT"])
def test_canonical_asset_symbol_rejects_out_of_bounds_or_unsafe_base(symbol):
    assert news.canonical_asset_symbol(symbol) == ""
