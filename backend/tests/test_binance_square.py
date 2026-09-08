"""Public Square feed contracts captured from the hashtag Latest tab."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
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


def test_post_metadata_keeps_internal_body_separate_from_short_headline():
    raw = post(content="$CHIP holds support ahead of another move.\n\nEntry: 0.05\nStop: 0.04")
    items = square.parse_posts([raw], "CHIP")
    assert len(items) == 1
    item = items[0]
    assert item["community_post_id"] == "123"
    assert item["content_type"] == "community"
    assert item["source"] == "Binance Square"
    assert item["author"] == "Example author"
    assert item["title"] == "$CHIP holds support ahead of another move."
    assert item["community_body"] == raw["content"]
    assert item["community_body_hash"] == hashlib.sha256(raw["content"].encode()).hexdigest()
    assert item["community_body_status"] == "ready"
    assert item["community_body_truncated"] is False
    assert datetime.fromisoformat(item["published"]).tzinfo is not None


def test_body_cleans_markup_without_dropping_levels_or_later_paragraphs():
    raw = post(content='<p>$CHIP holds support.</p><p>Entry: 0.05<br>Stop: 0.04 &amp; risk: 2%</p>'
                       '<script>untrusted()</script>\n{future}(CHIPUSDT)')
    item = square.parse_posts([raw], "CHIP")[0]
    assert item["community_body"] == "$CHIP holds support.\nEntry: 0.05\nStop: 0.04 & risk: 2%"
    reformatted = post(content="$CHIP holds support.\nEntry: 0.05\nStop: 0.04 & risk: 2%")
    assert item["community_body_hash"] == square.parse_posts([reformatted], "CHIP")[0]["community_body_hash"]
    changed = post(content=reformatted["content"].replace("0.04", "0.03"))
    assert item["community_body_hash"] != square.parse_posts([changed], "CHIP")[0]["community_body_hash"]


def test_large_non_ascii_bodies_fit_real_repository_serialization(provider, monkeypatch):
    monkeypatch.setenv("BINANCE_SQUARE_MAX_ITEMS", "10")
    rows = [post(str(i), authorName=f"Author {i}", content=f"$CHIP observation {i}\n" + '가격🙂"' * 10_000)
            for i in range(10)]
    transport(monkeypatch, lambda request: httpx.Response(200, json=feed(rows)))
    result = square.fetch_posts("CHIP")
    assert len(result["items"]) == 10
    assert all(item["community_body_truncated"] for item in result["items"])
    payload = provider.entries[square._PREFIX + "CHIP"][0]
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()) < 256_000
    for item in result["items"]:
        assert len(item["community_body"]) <= 20_000
        assert item["community_body_hash"] == hashlib.sha256(item["community_body"].encode()).hexdigest()
    square._cache.clear()
    monkeypatch.setattr(square, "get_http_client", lambda: pytest.fail("persisted bodies must survive restart"))
    assert square.fetch_posts("CHIP")["items"] == result["items"]


def detail(identifier="123", body="$CHIP has support at 0.05.\nThe stop level is 0.04.", **changes):
    return {"success": True, "code": "000000", "data": {
        "id": int(identifier), "contentType": 2, "contentStatus": 2,
        "bodyTextOnly": body, "body": '{"layout":{"root":[]}}', **changes}}


def test_long_article_uses_public_detail_body_instead_of_missing_content(provider, monkeypatch):
    calls = []
    article = post(contentType=2, title="$CHIP market outlook", content=None)
    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers and "cookie" not in request.headers
        if request.url.path.endswith("queryByHashtag"):
            return httpx.Response(200, json=feed([article]))
        assert str(request.url) == square._DETAIL_ENDPOINT + "123"
        return httpx.Response(200, json=detail())
    transport(monkeypatch, handler)
    result = square.fetch_posts("CHIP")
    item = result["items"][0]
    assert len(calls) == 2
    assert item["community_body"] == detail()["data"]["bodyTextOnly"]
    assert item["community_body_status"] == "ready"
    assert item["community_body_truncated"] is False
    assert result["source"]["details_fetched"] == 1
    assert result["source"]["body_pending_count"] == 0


def test_article_preview_and_title_are_never_treated_as_a_complete_body():
    item = square.parse_posts([post(contentType=2, title="$CHIP full article", content="Preview only")], "CHIP")[0]
    assert item["community_body"] == item["community_body_hash"] == ""
    assert item["community_body_status"] == "missing"
    assert item["community_body_truncated"] is True


def test_all_selected_articles_get_detail_and_share_it_across_tickers(provider, monkeypatch):
    calls = []
    rows = [post(str(i), authorName=f"Author {i}", contentType=2, title=f"CHIP and BTC outlook {i}", content=None)
            for i in range(5)]
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("queryByHashtag"):
            return httpx.Response(200, json=feed(rows))
        return httpx.Response(200, json=detail(request.url.path.rsplit("/", 1)[-1]))
    transport(monkeypatch, handler)
    first = square.fetch_posts("CHIP")
    assert first["source"]["details_fetched"] == 5
    assert all(item["community_body_status"] == "ready" for item in first["items"])
    square._cache.clear()
    second = square.fetch_posts("BTC")
    assert len(calls) == 7  # two ticker lists, each post body fetched once
    assert [item["community_body_hash"] for item in second["items"]] == [item["community_body_hash"] for item in first["items"]]


def test_expired_detail_refreshes_an_edited_body_and_changes_summary_identity(provider, monkeypatch):
    body = ["$CHIP stop level is 0.04."]
    def handler(request):
        return httpx.Response(200, json=feed([post(contentType=2, title="$CHIP article", content=None)])
                              if request.url.path.endswith("queryByHashtag") else detail(body=body[0]))
    transport(monkeypatch, handler)
    before = square.fetch_posts("CHIP")["items"][0]
    for entry in provider.entries.values():
        entry[0]["expires_at"] = 0
    square._cache.clear()
    body[0] = "$CHIP stop level is 0.03."
    after = square.fetch_posts("CHIP")["items"][0]
    assert after["community_body"] == body[0]
    assert before["community_body_hash"] != after["community_body_hash"]


@pytest.mark.parametrize("payload", [detail("456"), detail(contentStatus=0), detail(body=None)])
def test_invalid_or_absent_detail_keeps_title_without_inventing_body(provider, monkeypatch, payload):
    def handler(request):
        return httpx.Response(200, json=feed([post(contentType=2, title="$CHIP article", content=None)])
                              if request.url.path.endswith("queryByHashtag") else payload)
    transport(monkeypatch, handler)
    result = square.fetch_posts("CHIP")
    item = result["items"][0]
    assert item["title"] == "$CHIP article"
    assert item["community_body"] == item["community_body_hash"] == ""
    assert item["community_body_status"] in {"missing", "error"}
    assert result["source"]["status"] == "partial"
    assert result["source"]["body_pending_count"] == 1


def test_detail_429_stops_following_details_and_shares_existing_cooldown(provider, monkeypatch):
    calls = []
    rows = [post(str(i), authorName=f"Author {i}", contentType=2, title=f"CHIP article {i}", content=None) for i in range(3)]
    def handler(request):
        calls.append(request.url.path)
        return (httpx.Response(200, json=feed(rows)) if request.url.path.endswith("queryByHashtag")
                else httpx.Response(429, headers={"Retry-After": "600"}))
    transport(monkeypatch, handler)
    result = square.fetch_posts("CHIP")
    assert len(calls) == 2
    assert len(result["items"]) == 3
    assert all(item["community_body_status"] == "error" for item in result["items"])
    square._cache.clear()
    assert square.fetch_posts("BTC")["source"]["error"] == "rate_limited"
    assert len(calls) == 2


def test_v1_metadata_cache_refreshes_but_existing_rate_limit_does_not(provider, monkeypatch):
    old_key = "binance-square-latest-v1:CHIP"
    provider.entries[old_key] = ({"expires_at": time.time() + 300,
        "result": {"items": [{"title": "old title only"}], "source": {}}}, int((time.time() + 600) * 1000))
    calls = []
    transport(monkeypatch, lambda request: (calls.append(request) or httpx.Response(200, json=feed([post()]))))
    assert square.fetch_posts("CHIP")["items"][0]["community_body_status"] == "ready"
    assert len(calls) == 1
    assert square._COOLDOWN == "binance-square-latest-v1:cooldown"


def test_detail_network_failure_has_no_retry_or_fake_body(provider, monkeypatch):
    calls = []
    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("queryByHashtag"):
            return httpx.Response(200, json=feed([post(contentType=2, title="$CHIP article", content=None)]))
        raise httpx.ConnectError("fixture unavailable")
    transport(monkeypatch, handler)
    result = square.fetch_posts("CHIP")
    assert len(calls) == 2
    assert result["items"][0]["community_body_status"] == "error"
    assert result["items"][0]["community_body"] == ""
    assert result["source"]["detail_error"] == "detail_failed"


def test_detail_budget_is_shared_and_unfinished_articles_go_first_next_refresh(provider, monkeypatch):
    clock, calls = [100.0], []
    monkeypatch.setattr(square.time, "monotonic", lambda: clock[0])
    rows = [post(str(i), authorName=f"Author {i}", contentType=2, title=f"CHIP outlook {i}", content=None)
            for i in range(5)]
    def read(url, deadline, *, params=None):
        if url == square._ENDPOINT:
            return httpx.Response(200), {"feedData": rows}
        identifier = url.rsplit("/", 1)[-1]
        assert clock[0] < deadline
        calls.append(identifier)
        # A complete detail can use the remaining budget, after which another
        # upstream request must not be started.
        clock[0] += min(3, deadline - clock[0])
        return httpx.Response(200), detail(identifier)["data"]
    monkeypatch.setattr(square, "_read_json", read)
    first = square.fetch_posts("CHIP")
    assert len(calls) == 2
    assert first["source"]["body_pending_count"] == 3
    unfinished = {item["community_post_id"] for item in first["items"] if item["community_body_status"] != "ready"}
    # Simulate expiry without altering wall-clock or the fixture's article dates.
    for key, entry in provider.entries.items():
        if key == square._PREFIX + "CHIP" or entry[0].get("result", {}).get("body", {}).get("community_body_status") != "ready":
            entry[0]["expires_at"] = 0
    square._cache.clear()
    second = square.fetch_posts("CHIP")
    assert set(calls[2:]).issubset(unfinished)
    assert second["source"]["body_pending_count"] == 1


def test_expired_successful_details_rotate_so_every_edited_body_refreshes(provider, monkeypatch):
    clock, calls, slow = [time.time()], [], [False]
    monkeypatch.setattr(square.time, "time", lambda: clock[0])
    monkeypatch.setattr(square.time, "monotonic", lambda: clock[0])
    rows = [post(str(i), authorName=f"Author {i}", date=clock[0]-60,
                 contentType=2, title=f"CHIP outlook {i}", content=None) for i in range(5)]

    def read(url, deadline, *, params=None):
        if url == square._ENDPOINT:
            return httpx.Response(200), {"feedData": rows}
        identifier = url.rsplit("/", 1)[-1]
        calls.append(identifier)
        assert clock[0] < deadline
        clock[0] += min(3 if slow[0] else 0.1, deadline-clock[0])
        return httpx.Response(200), detail(identifier, body=f"$CHIP body {identifier} {'edited' if slow[0] else 'original'}")["data"]

    monkeypatch.setattr(square, "_read_json", read)
    assert square.fetch_posts("CHIP")["source"]["body_pending_count"] == 0
    calls.clear()
    slow[0] = True
    for _ in range(3):
        clock[0] += 301
        square._cache.clear()
        before = len(calls)
        result = square.fetch_posts("CHIP")
        assert len(calls)-before == 2
        assert result["source"]["body_pending_count"] == 0
    assert set(calls) == {str(i) for i in range(5)}
    assert all("edited" in item["community_body"] for item in result["items"])


def test_mid_batch_detail_429_preserves_other_expired_last_good_bodies(provider, monkeypatch):
    clock, blocked, calls = [time.time()], [False], []
    monkeypatch.setattr(square.time, "time", lambda: clock[0])
    monkeypatch.setattr(square.time, "monotonic", lambda: clock[0])
    rows = [post(str(i), authorName=f"Author {i}", date=clock[0]-60,
                 contentType=2, title=f"CHIP outlook {i}", content=None) for i in range(3)]

    def read(url, deadline, *, params=None):
        if url == square._ENDPOINT:
            return httpx.Response(200), {"feedData": rows}
        calls.append(url)
        return ((httpx.Response(429, headers={"Retry-After": "600"}), None) if blocked[0]
                else (httpx.Response(200), detail(url.rsplit("/", 1)[-1])["data"]))

    monkeypatch.setattr(square, "_read_json", read)
    first = square.fetch_posts("CHIP")
    blocked[0] = True
    clock[0] += 301
    square._cache.clear()
    before = len(calls)
    refreshed = square.fetch_posts("CHIP")
    assert len(calls)-before == 1
    assert refreshed["source"]["stale"] is True
    assert refreshed["source"]["detail_error"] == "detail_http_error"
    assert [item["community_body_hash"] for item in refreshed["items"]] == [item["community_body_hash"] for item in first["items"]]
    assert refreshed["source"]["body_pending_count"] == 0


def test_inflight_detail_does_not_wait_past_other_tickers_budget(provider, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    started, release = threading.Event(), threading.Event()
    def read(url, deadline, *, params=None):
        started.set()
        assert release.wait(2)
        return httpx.Response(200), detail()["data"]
    monkeypatch.setattr(square, "_read_json", read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        leader = pool.submit(square._fetch_body, "123", "CHIP", time.monotonic() + 6)
        assert started.wait(1)
        try:
            follower = square._fetch_body("123", "BTC", time.monotonic() + 0.01)
            assert follower["source"]["attempted"] is False
            assert follower["source"]["error"] == "detail_in_flight"
        finally:
            release.set()
        assert leader.result(timeout=2)["body"]["community_body_status"] == "ready"


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
