"""Public Square feed contracts captured from the hashtag Latest tab."""
from copy import deepcopy
from datetime import datetime, timezone
import time

import httpx
import pytest

from app import binance_square as square
from app import news
from app.http_runtime import SingleFlightGroup


def post(post_id="123", **changes):
    return {"id": post_id, "contentType": 1, "contentStatus": 2,
            "title": None, "content": "$CHIP holds support ahead of another move.\n\nEntry: 0.05\n#CHIP",
            "authorName": "Example author", "date": time.time() - 60,
            "webLink": f"https://www.binance.com/en/square/post/{post_id}",
            "hashtagList": ["#CHIP "], **changes}


def feed(rows):
    return {"success": True, "code": "000000", "data": {"feedData": rows}}


class Repository:
    def __init__(self):
        self.entries = {}

    def load_browser_pages(self, keys):
        return {k: deepcopy(self.entries[k][0]) for k in keys
                if k in self.entries and self.entries[k][1] > time.time() * 1000}

    def store_browser_pages(self, entries):
        self.entries.update(deepcopy(entries))


@pytest.fixture
def provider(monkeypatch):
    repo = Repository()
    monkeypatch.setenv("BINANCE_SQUARE_ENABLED", "true")
    monkeypatch.setattr(square, "_repository", lambda: repo)
    monkeypatch.setattr(square, "_cache", {})
    monkeypatch.setattr(square, "_flights", SingleFlightGroup())
    return repo


def transport(monkeypatch, handler):
    monkeypatch.setattr(square, "get_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


def test_post_metadata_and_short_headline_do_not_store_full_body():
    raw = post(content="$CHIP holds support ahead of another move.\n\nFULL BODY MUST NOT BE STORED")
    items = square.parse_posts([raw], "CHIP")
    assert len(items) == 1
    item = items[0]
    assert item["community_post_id"] == "123"
    assert item["content_type"] == "community"
    assert item["source"] == "Binance Square"
    assert item["author"] == "Example author"
    assert item["title"] == "$CHIP holds support ahead of another move."
    assert "FULL BODY" not in str(item)
    assert datetime.fromisoformat(item["published"]).tzinfo is not None


def test_parser_rejects_wrong_ticker_unknown_dates_removed_posts_and_bad_urls():
    rows = [post("1", date=None), post("2", date=True), post("3", date=float("nan")),
            post("4", date=time.time() - 8 * 86400), post("5", date=time.time() + 86400),
            post("6", contentStatus=0), post("7", webLink="https://evil.example/square/post/7"),
            post("8", content="$CHIPMUNK is in motion", hashtagList=["#CHIPMUNK"]),
            post("9", content="#CHIP #BTC #ETH"), post("10", content="$CHIP Join my VIP Telegram group now")]
    assert square.parse_posts(rows, "CHIP") == []


def test_latest_newest_first_dedupe_and_author_diversity():
    rows = [post(str(i), date=time.time() - i, content=f"$CHIP market observation number {i}") for i in range(10)]
    rows += [post("20", authorName="Different author", date=time.time()-2)]
    items = square.parse_posts([*rows, rows[0]], "CHIP")
    assert len({i["community_post_id"] for i in items}) == len(items)
    assert len([i for i in items if i["author"] == "Example author"]) <= 2
    assert any(i["author"] == "Different author" for i in items)
    assert [i["published"] for i in items] == sorted([i["published"] for i in items], reverse=True)


def test_direct_public_latest_request_and_shared_db_cache(provider, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.params["orderBy"] == "LATEST"
        assert request.url.params["hashtag"] == "#chip"
        assert "authorization" not in request.headers
        assert "cookie" not in request.headers
        return httpx.Response(200, json=feed([post()]))
    transport(monkeypatch, handler)
    first = square.fetch_posts("CHIP")
    assert first["source"]["status"] == "ready"
    assert len(first["items"]) == 1
    square._cache.clear()
    second = square.fetch_posts("CHIP")
    assert len(calls) == 1
    assert second["source"]["cached"] is True
    assert second["source"]["attempted"] is False


def test_429_cooldown_is_shared_across_tickers_and_restart(provider, monkeypatch):
    calls = []
    transport(monkeypatch, lambda request: (calls.append(request) or httpx.Response(429, headers={"Retry-After": "600"})))
    first = square.fetch_posts("CHIP")
    square._cache.clear()
    second = square.fetch_posts("BTC")
    assert len(calls) == 1
    assert first["source"]["error"] == second["source"]["error"] == "rate_limited"
    assert second["source"]["source_page"].endswith("/btc")
    assert second["source"]["attempted"] is False


def test_valid_community_survives_news_filters_without_displacing_articles():
    community = square.parse_posts([post()], "CHIP")[0]
    community["title"] = "CHIP LONG SETUP Entry 0.05 Stop 0.04"
    articles = [{"title": f"CHIP news {i}", "url": f"https://example.com/{i}",
                 "published": datetime.now(timezone.utc).isoformat()} for i in range(10)]
    result = news._public_news_candidates([community, *articles], limit=10, include_archive=True)
    assert len(result) == 11
    assert community in result
    forged = {**community, "url": "https://evil.example/post/123"}
    assert not news._is_news_article_candidate(forged)


def test_community_repost_does_not_displace_original_article():
    community = square.parse_posts([post()], "CHIP")[0]
    article = {"title": community["title"], "source": "CoinDesk", "url": "https://example.com/article"}
    result = news._public_news_candidates([community, article], limit=10, include_archive=True)
    assert len(result) == 2


def test_same_line_ticker_tag_footer_does_not_establish_relevance():
    raw = post(content="Zcash consensus network processing speed has increased. $BTC #BTC", hashtagList=["#BTC"])
    assert square.parse_posts([raw], "BTC") == []


def test_directional_prose_is_translated_while_ticker_stays_intact():
    title = "LONG $CFG structure is readable"
    assert news._translation_protected_upper_tokens(title) == ("CFG",)
    assert not news._valid_title_translation(title, "LONG $CFG 구조가 명확하다")
    assert news._valid_title_translation(title, "$CFG 롱 포지션 구조가 명확하다")


def test_community_passes_translation_pipeline(monkeypatch):
    item = square.parse_posts([post()], "CHIP")[0]
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda items: [
        {**i, "original_title": i["title"], "title": "CHIP 지지선 유지 의견"} for i in items])
    result = news._localize_news_payload({"symbol": "CHIP", "items": [item]})
    assert result["items"][0]["community_post_id"] == "123"
    assert result["items"][0]["title"] == "CHIP 지지선 유지 의견"


def test_disabled_source_never_accesses_http_or_db(monkeypatch):
    monkeypatch.setenv("BINANCE_SQUARE_ENABLED", "false")
    monkeypatch.setattr(square, "_repository", lambda: pytest.fail("unexpected DB access"))
    monkeypatch.setattr(square, "get_http_client", lambda: pytest.fail("unexpected HTTP access"))
    assert square.fetch_posts("CHIP")["source"]["status"] == "disabled"


def test_second_public_page_when_first_contains_only_other_coin_tags(provider, monkeypatch):
    calls = []
    def handler(request):
        page = int(request.url.params["pageIndex"])
        calls.append(page)
        rows = [post(str(i), content="Zcash network transaction discussion.\n$CHIP #CHIP") for i in range(20)] if page == 1 else [post("100")]
        return httpx.Response(200, json=feed(rows))
    transport(monkeypatch, handler)
    result = square.fetch_posts("CHIP")
    assert calls == [1, 2]
    assert result["source"]["pages_fetched"] == 2
    assert [i["community_post_id"] for i in result["items"]] == ["100"]


def test_second_page_failure_preserves_first_page_and_reports_partial(provider, monkeypatch):
    calls = []
    def handler(request):
        page = int(request.url.params["pageIndex"])
        calls.append(page)
        return httpx.Response(500) if page == 2 else httpx.Response(200, json=feed([post(str(i)) for i in range(20)]))
    transport(monkeypatch, handler)
    result = square.fetch_posts("CHIP")
    assert calls == [1, 2]
    assert result["source"]["status"] == "partial"
    assert result["source"]["http_status"] == 500
    assert result["items"]


def test_expired_memory_cache_rechecks_fresh_shared_cache(provider, monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json=feed([post()])))
    square.fetch_posts("CHIP")
    square._cache[square._PREFIX + "CHIP"]["expires_at"] = 0
    monkeypatch.setattr(square, "get_http_client", lambda: pytest.fail("another worker already refreshed DB cache"))
    assert square.fetch_posts("CHIP")["source"]["cached"] is True


def test_outage_retains_last_good_community_with_stale_flag(provider, monkeypatch):
    transport(monkeypatch, lambda request: httpx.Response(200, json=feed([post()])))
    square.fetch_posts("CHIP")
    key = square._PREFIX + "CHIP"
    square._cache[key]["expires_at"] = provider.entries[key][0]["expires_at"] = 0
    transport(monkeypatch, lambda request: httpx.Response(503))
    result = square.fetch_posts("CHIP")
    assert result["items"]
    assert result["source"]["stale"] is True
    assert result["source"]["status"] == "partial"


def test_all_rss_fail_but_community_still_publishes(monkeypatch):
    monkeypatch.setenv("BINANCE_SQUARE_ENABLED", "true")
    monkeypatch.setattr(news, "_prepare_asset_identity", lambda _: None)
    def fail(*args, **kwargs):
        raise news.NewsFetchError("fixture RSS unavailable")
    monkeypatch.setattr(news, "_coin_news_envelope", fail)
    monkeypatch.setattr(news, "_fetch_coindesk_news", fail)
    item = square.parse_posts([post()], "CHIP")[0]
    monkeypatch.setattr(square, "fetch_posts", lambda symbol: {"items": [item], "source": {"name": "binance_square", "status": "ready"}})
    result = news.fetch_coin_news_for_collector("CHIP")
    assert result["items"] == [item]
    assert any(s["name"] == "binance_square" for s in result["sources"])


def test_public_fallback_includes_community_alongside_rss(monkeypatch):
    monkeypatch.setenv("BINANCE_SQUARE_ENABLED", "true")
    monkeypatch.setattr(news, "_coin_news_envelope", lambda *a, **k: {"items": [{"title": "CHIP 프로젝트 소식"}]})
    item = square.parse_posts([post()], "CHIP")[0]
    monkeypatch.setattr(square, "fetch_posts", lambda symbol: {"items": [item], "source": {"name": "binance_square", "status": "ready"}})
    result = news._fetch_public_news_payload("CHIP")
    assert len(result["items"]) == 2
    assert item in result["items"]
