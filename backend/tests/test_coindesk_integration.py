from datetime import datetime, timezone

import pytest

from app import coindesk_api, news


def _article(title="Ethena token update", published=None):
    return {"title": title, "source": "CoinDesk", "url": "https://www.coindesk.com/markets/story",
            "published": published or datetime.now(timezone.utc).isoformat()}


def test_official_api_recovers_initial_news_when_all_rss_fail(monkeypatch):
    def fail(*_args, **_kwargs):
        raise news.NewsFetchError("RSS unavailable")
    monkeypatch.setattr(news, "_coin_news_envelope", fail)
    monkeypatch.setattr(news, "_fetch_coindesk_news", fail)
    monkeypatch.setattr(coindesk_api, "configuration", lambda: {"enabled": True})
    queries = []
    def fetch(term):
        queries.append(term)
        return {"items": [_article(), _article("Bitcoin news"), _article(published="2020-01-01T00:00:00Z")],
                "source": {"name": "coindesk_news_api", "status": "ready", "fetched_count": 3}}
    monkeypatch.setattr(coindesk_api, "fetch_news", fetch)
    payload = news.fetch_coin_news_for_collector("ENA")
    assert queries == ["ethena"]
    assert len(payload["items"]) == 1
    assert payload["items"][0]["feed_source"] == "coindesk_news_api"
    assert payload["sources"][-1]["excluded_count"] == 2


@pytest.mark.parametrize("api_status", ["ready", "empty"])
def test_official_response_replaces_only_coindesk_html(monkeypatch, api_status):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    visited = []
    def fetch(descriptors):
        visited.extend(descriptors)
        return {news._browser_page_key(page): {"items": [], "status": "empty"} for page in descriptors}
    monkeypatch.setattr(news, "_cached_browser_pages", fetch)
    payload = news.enrich_coin_news_for_collector("ENA", {"items": [], "sources": [
        {"name": "coindesk_news_api", "status": api_status}]})
    assert visited and all(page["publisher"] != "CoinDesk" for page in visited)
    assert payload["browser_enrichment"]["status"] == "ready"
    replaced = [source for source in payload["sources"] if source.get("status") == "replaced"]
    assert replaced and all(source["attempted"] is False for source in replaced)


def test_api_failure_keeps_independent_browser_fallback(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    visited = []
    def fetch(descriptors):
        visited.extend(descriptors)
        return {news._browser_page_key(page): {"items": [], "status": "empty"} for page in descriptors}
    monkeypatch.setattr(news, "_cached_browser_pages", fetch)
    news.enrich_coin_news_for_collector("ENA", {"items": [], "sources": [
        {"name": "coindesk_news_api", "status": "error"}]})
    assert any(page["publisher"] == "CoinDesk" for page in visited)


def test_unconfigured_api_makes_no_request(monkeypatch):
    monkeypatch.delenv("COINDESK_API_KEY", raising=False)
    monkeypatch.setattr(news, "_coin_news_envelope", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_kwargs: [])
    monkeypatch.setattr(coindesk_api, "fetch_news", lambda *_args: (_ for _ in ()).throw(AssertionError()))
    assert news.fetch_coin_news_for_collector("ENA")["items"] == []


def test_supplemental_free_rss_recovers_from_primary_outage(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_EXTRA_RSS_ENABLED", "true")
    monkeypatch.delenv("COINDESK_API_KEY", raising=False)
    def fail(*_args, **_kwargs):
        raise news.NewsFetchError("unavailable")
    monkeypatch.setattr(news, "_coin_news_envelope", fail)
    monkeypatch.setattr(news, "_fetch_coindesk_news", fail)
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", lambda source, **_kwargs: [
        {**_article(), "source": source, "url": f"https://example.com/{source}"}])
    payload = news.fetch_coin_news_for_collector("ENA")
    assert payload["items"]
    assert {source["name"] for source in payload["sources"] if source["status"] == "ready"} == {
        "decrypt_rss", "cryptoslate_rss"}


def test_publisher_rss_cache_survives_process_memory_loss(monkeypatch):
    from types import SimpleNamespace
    saved, requests = {}, []
    xml = '<rss><channel><item><title>Ethena update</title><link>https://decrypt.co/1/ethena</link><pubDate>Mon, 07 Sep 2026 07:00:00 GMT</pubDate></item></channel></rss>'
    def get(url, **_kwargs):
        requests.append(url)
        return SimpleNamespace(text=xml, raise_for_status=lambda: None)
    monkeypatch.setattr(news, "_publisher_rss_cache", {})
    monkeypatch.setattr(news, "get_http_client", lambda: SimpleNamespace(get=get))
    monkeypatch.setattr(news, "_load_durable_browser_pages", lambda keys: {key: saved[key] for key in keys if key in saved})
    monkeypatch.setattr(news, "_store_durable_browser_pages", lambda entries: saved.update({key: payload for key, (payload, _ttl) in entries.items()}))
    first = news._fetch_shared_publisher_rss("decrypt_rss")
    news._publisher_rss_cache.clear()
    second = news._fetch_shared_publisher_rss("decrypt_rss")
    assert first == second
    assert first[0]["source"] == "Decrypt"
    assert requests == ["https://decrypt.co/feed"]


def test_concert_listing_does_not_spend_translation_or_analysis_budget():
    assert not news._is_news_article_candidate({"title": "10,000 Maniacs at Celestia Theater"})
    assert news._is_news_article_candidate({"title": "NFT tickets launch at Celestia Theater"})


@pytest.mark.parametrize("title", [
    "0.05453 | CHIPUSDT USDⓈ-Margined Perpetual Chart | Binance Futures",
    "Ethena Price, ENA Price, Live Charts, and Marketcap: ethena crypto",
    "Celestia Kae Watts Obituary (2026)",
    "Jasmine Chong & Jason Tabalujan: Building Celestia to Bring Their Family Home",
])
def test_false_project_matches_are_not_news_articles(title):
    assert not news._is_news_article_candidate({"title": title})


def test_cryptoslate_xrp_uses_verified_publisher_slug():
    page = next(page for page in news._browser_news_pages("XRP", "리플")
                if page["name"] == "cryptoslate_asset_topic")
    assert page["url"] == "https://cryptoslate.com/news/xrp/"


@pytest.mark.parametrize("title,source", [
    ("Bitcoin Futures", "CME Group"),
    ("Bitcoin Futures", "cmegroup.com"),
    ("$CHIP 🟢 LONG SCENARIO 🎯 Entry: $0.0515–0.0520 🛡️ Stop | LuckyStar", "Binance"),
    ("1.217 ORCA/USDC 현물 거래 | 암호화폐, 주식 및 원자재", "Binance"),
    ("1.217 Trade ORCA/USDC Spot | Crypto, bStocks & tCommodities", "Binance"),
    ("Orca/usdt is going to pump today", "Binance"),
    ("$Celestia (TIA.CC)$", "Moomoo"),
    ("$PROM PROM is showing bearish movement: -5.16% | HALIFI on Binance Square", "Binance"),
    ("Property Share Investment Trust-Propshare Celestia Dividend History, Yield & Record Date", "INDmoney"),
    ("Kids showcase talent at ‘Sapiens Celestia’", "The Tribune"),
    ("ICP Crypto Price Prediction: Can Internet Computer Return To $20? Rb Leipzig (wdBc4vFTzb)", "Mshale"),
    ("INSIGHTS⚡️ Internet Computer canisters are designed to host", "KuCoin"),
])
def test_live_google_results_exclude_products_and_community_trade_setups(title, source):
    assert not news._is_news_article_candidate({"title": title, "source": source,
                                               "url": "https://news.google.com/rss/articles/example"})


def test_publisher_news_is_preserved_while_evergreen_and_signals_are_excluded():
    assert news._is_news_article_candidate({"title": "CME Group Bitcoin Futures volume reaches new record", "source": "CME Group"})
    assert news._is_news_article_candidate({"title": "Binance lists CHIP token for spot trading", "source": "Binance"})


def test_live_unknown_ticker_does_not_search_five_year_archive(monkeypatch):
    calls = []
    monkeypatch.setattr(news, "_fetch_news", lambda query, **_kwargs: calls.append(query) or [])
    assert news._coin_news_envelope("CHIP", strict=True, relevant_only=True)["items"] == []
    assert len(calls) == 2 and all("when:5y" not in query for query in calls)


def test_known_old_google_results_cannot_displace_current_headlines(monkeypatch):
    old = [{**_article("Ethena token archive", published="2020-01-01T00:00:00Z"),
            "title": f"Ethena token archive {index}"} for index in range(12)]
    current = _article("Ethena announces new integration")
    monkeypatch.setattr(news, "_fetch_news", lambda *_args, **_kwargs: old + [current])
    result = news._coin_news_envelope("ENA", strict=True, relevant_only=True)
    assert [item["title"] for item in result["items"]] == [current["title"]]


def test_cached_old_news_is_removed_before_translation(monkeypatch):
    monkeypatch.setattr(news, "_ensure_title_translations", lambda *_args: (_ for _ in ()).throw(AssertionError()))
    payload = news._localize_news_payload({"items": [_article(published="2025-12-01T00:00:00Z")]})
    assert payload["items"] == []
    assert payload["translation"]["pending_count"] == 0
