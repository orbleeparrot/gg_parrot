"""Public briefing recovery uses free feeds without live HTTP, AI or a database."""
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from app import news
from app.http_runtime import SingleFlightGroup


def article(title="Internet Computer blockchain update", *, url="https://news.test/current", days=0):
    return {"title": title, "source": "CoinDesk", "url": url,
            "published": (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(news, "_coin_envelope_cache", {}, raising=False)
    monkeypatch.setattr(news, "_coin_refresh_threads", {}, raising=False)
    monkeypatch.setattr(news, "_cache", {})
    monkeypatch.setattr(news, "_coin_cache", {})
    monkeypatch.setattr(news, "_publisher_rss_cache", {})
    monkeypatch.setattr(news, "_coindesk_cache", None)
    monkeypatch.setattr(news, "_coindesk_error_cache", None)
    monkeypatch.setattr(news, "_rss_refreshes", SingleFlightGroup())
    monkeypatch.setattr(news, "_coin_refreshes", SingleFlightGroup())
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _: None)
    monkeypatch.setattr(news, "_load_durable_browser_pages", lambda _: {})
    monkeypatch.setattr(news, "_store_durable_browser_pages", lambda _: None)
    monkeypatch.setattr(news, "_load_durable_market_summary", lambda _: None)
    monkeypatch.setattr(news, "_summarize", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(news, "get_http_client", lambda: pytest.fail("Unexpected outbound HTTP"))
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_: [])
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", lambda *_args, **_kwargs: [])
    # Keep the real public localization envelope and its pending/raw-cache path.
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda rows: [
        {**row, "title": "한국어 뉴스", "original_title": row["title"]} for row in rows])


@pytest.mark.parametrize("market", [False, True])
def test_usable_google_news_does_not_request_extra_publishers(monkeypatch, market):
    monkeypatch.setattr(news, "_fetch_public_news_fallback", lambda *_: pytest.fail("Google already supplied news"))
    monkeypatch.setattr(news, "_coin_news_envelope", lambda *_args, **_kwargs: {"items": [article()]})
    monkeypatch.setattr(news, "_fetch_news", lambda *_args, **_kwargs: [article()])
    result = news.get_market_news() if market else news.get_coin_news("ICPUSDT")
    assert result["items"][0]["title"] == "한국어 뉴스"
    assert [source["name"] for source in result["sources"]] == ["google_news_rss"]


@pytest.mark.parametrize("market", [False, True])
@pytest.mark.parametrize("google_failed", [False, True])
def test_empty_or_failed_google_recovers_from_free_feeds(monkeypatch, market, google_failed):
    def google(*_args, **_kwargs):
        if google_failed:
            raise news.NewsFetchError("Google unavailable")
        return [] if market else {"items": []}
    monkeypatch.setattr(news, "_coin_news_envelope", google)
    monkeypatch.setattr(news, "_fetch_news", google)
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_: [article()])
    result = news.get_market_news() if market else news.get_coin_news("ICP")
    assert len(result["items"]) == 1
    assert result["items"][0]["original_title"] == "Internet Computer blockchain update"
    assert result["translation"]["status"] == "ready"
    sources = {source["name"]: source for source in result["sources"]}
    assert sources["google_news_rss"]["status"] == ("error" if google_failed else "ready")
    assert sources["coindesk_rss"]["status"] == "ready"
    assert len(sources) == 4


def test_fallback_filters_asset_dates_and_duplicates_before_cap(monkeypatch):
    rows = [article(f"Internet Computer blockchain update {index}", url=f"https://news.test/{index}", days=index)
            for index in range(15)]
    rows += [article("Ethereum blockchain update", url="https://news.test/eth"),
             article("Internet Computer old news", url="https://news.test/old", days=31),
             article("Internet Computer future news", url="https://news.test/future", days=-2),
             article("Different Internet Computer headline", url="https://news.test/0?utm_source=other")]
    monkeypatch.setattr(news, "_fetch_coindesk_news", lambda **_: rows)
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", lambda *_args, **_kwargs: list(reversed(rows)))
    result = news._fetch_public_news_fallback("ICP")
    assert len(result["items"]) == 10
    assert len({item["url"].split("?")[0] for item in result["items"]}) == 10
    assert all("Ethereum" not in item["title"] for item in result["items"])
    assert all(news._within_live_news_window(item) for item in result["items"])
    assert len(news._fetch_public_news_fallback()["items"]) == 8


def fail(*_args, **_kwargs):
    raise news.NewsFetchError("Upstream unavailable")


@pytest.mark.parametrize("market", [False, True])
def test_every_source_failing_raises_with_safe_source_metadata(monkeypatch, market):
    monkeypatch.setattr(news, "_coin_news_envelope", fail)
    monkeypatch.setattr(news, "_fetch_news", fail)
    monkeypatch.setattr(news, "_fetch_coindesk_news", fail)
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", fail)
    with pytest.raises(news.NewsFetchError) as error:
        news.get_market_news() if market else news.get_coin_news("ICP")
    assert len(error.value.sources) == 4
    assert all(source["status"] == "error" for source in error.value.sources)
    assert "coin:ICP" not in news._coin_cache


def test_valid_empty_google_remains_distinct_from_failed_publishers(monkeypatch):
    monkeypatch.setattr(news, "_coin_news_envelope", lambda *_args, **_kwargs: {"items": []})
    monkeypatch.setattr(news, "_fetch_coindesk_news", fail)
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", fail)
    result = news.get_coin_news("ICP")
    assert result["items"] == []
    assert result["sources"][0]["status"] == "ready"
    assert all(source["status"] == "error" for source in result["sources"][1:])


def test_total_outage_retains_stale_snapshot_and_current_failures(monkeypatch):
    stored = {"snapshot_id": "previous-snapshot", "news_payload": {"items": [article(days=5)]},
              "collection": {"last_success_ms": 1}}
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _: stored)
    monkeypatch.setattr(news, "_coin_news_envelope", fail)
    monkeypatch.setattr(news, "_fetch_coindesk_news", fail)
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", fail)
    result = news.get_coin_news("ICP")
    assert result["data_source"] == "prefect_db_stale" and result["stale"] is True
    assert result["snapshot_id"] == "previous-snapshot" and result["refreshing"] is True
    assert len(result["items"]) == 1
    thread = news._coin_refresh_threads.get("coin:ICP")
    if thread is not None:
        thread.join(timeout=5)
    result = news.get_coin_news("ICP")  # 배경 갱신이 끝난 뒤: 여전히 스냅샷이지만 현재 실패 소스가 붙는다
    assert result["data_source"] == "prefect_db_stale" and result["stale"] is True
    assert result["sources"] and all(source["status"] == "error" for source in result["sources"])


def test_shared_free_feed_cache_survives_different_public_tickers(monkeypatch):
    stamp = format_datetime(datetime.now(timezone.utc))
    xml = f"""<rss><channel><item><title>Internet Computer blockchain update</title>
    <link>https://www.coindesk.com/markets/2026/09/08/icp-update</link><pubDate>{stamp}</pubDate></item>
    <item><title>Bitcoin blockchain update</title><link>https://www.coindesk.com/markets/2026/09/08/btc-update</link>
    <pubDate>{stamp}</pubDate></item></channel></rss>"""
    # Restore only the actual shared feed readers replaced by the autouse fixture.
    real_coindesk, real_extra = ORIGINAL_READERS
    monkeypatch.setattr(news, "_fetch_coindesk_news", real_coindesk)
    monkeypatch.setattr(news, "_fetch_shared_publisher_rss", real_extra)
    monkeypatch.setattr(news, "_coin_news_envelope", lambda *_args, **_kwargs: {"items": []})
    calls = []
    def handle(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=xml)
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(news, "get_http_client", lambda: client)
        assert news.get_coin_news("ICP")["items"]
        assert news.get_coin_news("BTC")["items"]
    assert len(calls) == 3 and len(set(calls)) == 3


def test_google_http_failure_logging_never_includes_credentials_or_body(monkeypatch, caplog):
    request = httpx.Request("GET", "https://news.google.com/rss/search?token=private-credential")
    response = httpx.Response(429, request=request, text="private response body")
    def blocked(*_args, **_kwargs):
        raise httpx.HTTPStatusError("private-credential", request=request, response=response)
    class Client:
        get = staticmethod(blocked)
    monkeypatch.setattr(news, "get_http_client", Client)
    with pytest.raises(news.NewsFetchError):
        news._fetch_news("Bitcoin", strict=True)
    assert "HTTPStatusError" in caplog.text and "429" in caplog.text
    assert "private" not in caplog.text


ORIGINAL_READERS = news._fetch_coindesk_news, news._fetch_shared_publisher_rss
