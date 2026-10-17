"""Regional community preparation is source-isolated, bounded and read-only."""
from contextlib import nullcontext
from datetime import datetime
import json
import time

import httpx
import pytest
from sqlmodel import delete, select

from app import db as database, domestic_community as community, public_news
from tests.test_domestic_community import listings, row, unlock


@pytest.fixture(autouse=True)
def isolated_sources(monkeypatch):
    with database.get_session() as db:
        db.exec(delete(community.DomesticCommunityItem))
        db.exec(delete(community.DomesticCommunityState))
        db.exec(delete(public_news.PublicNewsLease).where(public_news.PublicNewsLease.scope == community.SCOPE))
        db.commit()
    community.clear_caches()
    monkeypatch.setenv("DOMESTIC_COMMUNITY_ENABLED", "false")


def tokenpost(title="비트코인 가격 이야기", number="123", *, board="crypto", date=None):
    date = date or datetime.now(community._KST).strftime("%Y.%m.%d")
    return f'''<section class=""><div class="board_list_title"><p class="list_item_title">
      <a href="/forum/{board}/{number}">{title}</a></p></div>
      <span class="community_nickname">private author</span><span class="community_date">{date}</span></section>'''


def coinpan(title="이더리움 반등 차트", number="123", *, date=None, classes=""):
    date = date or datetime.now(community._KST).strftime("%H:%M")
    return f'''<tr class="{classes}"><td class="title"><a href="/free/{number}">{title}</a>
      <a href="/free/{number}#comment">[123]</a></td><td class="author">private author</td>
      <td class="time"><span class="number">{date}</span></td></tr>'''


def mock_sources(monkeypatch, failed=()):
    calls = []
    def fetch(source_id):
        calls.append(source_id)
        if source_id in failed:
            raise httpx.ConnectTimeout("offline")
        return {"dcinside_bitcoin": row(), "tokenpost_crypto": tokenpost(),
                "tokenpost_chart": tokenpost(board="chart", number="124"),
                "coinpan_free": coinpan()}[source_id]
    monkeypatch.setattr(community, "fetch_source_listing", fetch)
    monkeypatch.setattr(community.krw, "list_symbols", lambda ex: {
        "items": listings()[ex]["items"], "fetched_at": time.time()})
    return calls


@pytest.mark.parametrize("source,html,assets,precision", [
    ("tokenpost_crypto", tokenpost(), ["BTC"], "day"),
    ("tokenpost_chart", tokenpost("이더리움 차트 분석", board="chart"), ["ETH"], "day"),
    ("coinpan_free", coinpan(), ["ETH"], "minute"),
])
def test_real_listing_shapes_extract_public_headlines_only(source, html, assets, precision):
    items, stats = community.parse_source_listing(source, html, listings())
    assert stats["matched"] == 1
    item = items[0]
    assert item["source_id"] == source and item["asset_symbols"] == assets
    assert item["published_precision"] == precision
    assert "author" not in json.dumps(item)
    assert "excerpt" not in item


def test_notice_ad_and_unrelated_rows_are_not_community_posts():
    html = coinpan("BTC 증정금 최대 500만원", classes="notice bg1")
    html += coinpan("비트코인 수익 보장 오픈카톡 입장", number="124")
    html += coinpan("BTC 코인 가입하고 추천인 수수료 할인", number="125")
    html += coinpan("오늘 점심 이야기", number="126")
    html += coinpan("BTC 가격은 어떻게 될까요", number="127")
    items, stats = community.parse_source_listing("coinpan_free", html, listings())
    assert len(items) == 1 and items[0]["url"].endswith("127")
    assert stats["irrelevant"] == 3
    html = tokenpost().replace('class=""', 'class="board_list_notice"')
    html += tokenpost(number="128")
    assert len(community.parse_source_listing("tokenpost_crypto", html, listings())[0]) == 1


@pytest.mark.parametrize("url", ["https://evil.test/free/123", "http://coinpan.com/free/123",
    "//127.0.0.1/free/123", "https://coinpan.com@evil.test/free/123", "/free/rss", "/free/abc",
    "https://coinpan.com:443/free/123", "/free/123/extra"])
def test_coinpan_canonical_allowlist(url):
    assert community.canonical_source_post_url("coinpan_free", url) is None


def test_cross_board_and_query_canonicalization_boundaries():
    assert community.canonical_source_post_url("tokenpost_crypto", "/forum/chart/123") is None
    assert community.canonical_source_post_url("tokenpost_crypto", "/forum/crypto/123?utm_source=x#abc") == "https://www.tokenpost.kr/forum/crypto/123"
    assert community.canonical_source_post_url("coinpan_free", "/free/123?page=1#comment") == "https://coinpan.com/free/123"
    with pytest.raises(ValueError):
        community.parse_source_listing("unknown", "<html/>", listings())
    with pytest.raises(ValueError, match="recognized"):
        community.parse_source_listing("tokenpost_crypto", "<html>captcha</html>", listings())


def test_partial_failure_keeps_other_sources_ready_and_old_items(monkeypatch):
    calls = mock_sources(monkeypatch)
    assert community.collect_once()["status"] == "ready"
    assert len(calls) == 4
    initial = community.get_domestic_community("upbit")
    assert len(initial["items"]) == 4
    unlock()
    with database.get_session() as db:
        state = db.get(community.DomesticCommunityState, community.SCOPE)
        sources = json.loads(state.sources_json)
        for source in sources:
            source["next_attempt_ms"] = 0
        state.sources_json = json.dumps(sources)
        db.add(state)
        db.commit()
    calls = mock_sources(monkeypatch, failed={"tokenpost_crypto"})
    result = community.collect_once()
    assert len(calls) == 4 and result["status"] == "partial"
    feed = community.get_domestic_community("upbit")
    assert feed["items"] == initial["items"]
    by_source = {s["id"]: s for s in feed["collection"]["sources"]}
    assert by_source["tokenpost_crypto"]["status"] == "error"
    assert by_source["coinpan_free"]["status"] == "ready"
    assert by_source["tokenpost_crypto"]["last_success_ms"] > 0


def test_source_cooldown_never_stops_catalog_refresh_or_fakes_source_attempts(monkeypatch):
    now = int(time.time() * 1000)
    catalog_calls = []
    sources = [{"id": source["id"], "status": "error", "failures": 3,
                "last_attempt_ms": now, "next_attempt_ms": now + 480_000,
                "last_success_ms": now - 60_000} for source in community.SOURCES]
    with database.get_session() as db:
        db.add(community.DomesticCommunityState(status="error", last_success_ms=now - 60_000,
            listings_json=json.dumps(listings(now - 301_000)), sources_json=json.dumps(sources)))
        db.add(community.DomesticCommunityItem(id="old", title="비트코인 가격", url="https://coinpan.com/free/123",
            source="코인판 자유게시판", published=community._iso(now - 60_000),
            published_ms=now - 60_000, collected_ms=now - 60_000, asset_symbols_json='["BTC"]'))
        db.commit()
    def catalog(ex):
        catalog_calls.append(ex)
        return {"items": listings(now)[ex]["items"], "fetched_at": now / 1000}
    monkeypatch.setattr(community.krw, "list_symbols", catalog)
    monkeypatch.setattr(community, "fetch_source_listing", lambda *_: pytest.fail("source cooldown ignored"))
    result = community.collect_once(now_ms=now)
    assert result["retry_seconds"] <= 60 and catalog_calls == ["upbit", "bithumb"]
    feed = community.get_domestic_community("upbit", now_ms=now)
    assert len(feed["items"]) == 1
    by_id = {s["id"]: s for s in feed["collection"]["sources"]}
    assert by_id["coinpan_free"]["last_attempt_ms"] == now
    assert by_id["coinpan_free"]["failures"] == 3
    assert by_id["coinpan_free"]["next_attempt_ms"] == now + 480_000


def test_search_source_and_limit_are_prepared_only_and_deterministic(monkeypatch):
    mock_sources(monkeypatch)
    community.collect_once()
    def forbidden(*_a, **_kw):
        pytest.fail("public read must not collect, fetch or call AI")
    monkeypatch.setattr(community, "fetch_source_listing", forbidden)
    monkeypatch.setattr(community.krw, "list_symbols", forbidden)
    feed = community.get_domestic_community("upbit", q="이더리움", source="coinpan_free", limit=1)
    assert len(feed["items"]) == 1 and feed["items"][0]["source_id"] == "coinpan_free"
    assert feed["search_scope"] == "prepared_headlines_last_7_days"
    assert feed["items"][0]["content_type"] == "community"
    assert feed["total"] == 1 and feed["limit"] == 1
    for kwargs in ({"source": "https://evil.test"}, {"q": "x" * 101}, {"limit": 101}):
        with pytest.raises(ValueError):
            community.get_domestic_community("upbit", **kwargs)


def test_http_fixed_urls_reject_redirects_oversized_and_unexpected_content(monkeypatch):
    class Client:
        def __init__(self, response):
            self.response = response
        def stream(self, method, url, **kwargs):
            assert kwargs["follow_redirects"] is False
            return nullcontext(self.response)
    request = httpx.Request("GET", "https://coinpan.com/free")
    for response in (httpx.Response(302, headers={"location": "https://evil.test"}, request=request),
                     httpx.Response(200, headers={"content-type": "application/json"}, content=b"{}", request=request),
                     httpx.Response(200, headers={"content-type": "text/html"}, content=b"a" * 65, request=request)):
        monkeypatch.setattr(community, "get_http_client", lambda: Client(response))
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            community._fetch_text("https://coinpan.com/free", max_bytes=64)
    with pytest.raises(ValueError, match="allowlisted"):
        community._fetch_text("https://169.254.169.254/latest/meta-data", max_bytes=64)


def test_each_source_robots_is_enforced_without_fetching_disallowed_list(monkeypatch):
    calls = []
    def fetch(url, **kwargs):
        calls.append(url)
        return "User-agent: *\nDisallow: /forum/chart"
    monkeypatch.setattr(community, "_source_robots_cache", {})
    monkeypatch.setattr(community, "_fetch_text", fetch)
    with pytest.raises(ValueError, match="robots"):
        community.fetch_source_listing("tokenpost_chart")
    assert calls == ["https://www.tokenpost.kr/robots.txt"]


def test_failed_sources_still_prune_expired_and_cap_shared_storage(monkeypatch):
    now = int(time.time() * 1000)
    mock_sources(monkeypatch, failed={source["id"] for source in community.SOURCES})
    monkeypatch.setattr(community, "MAX_ITEMS", 3)
    with database.get_session() as db:
        for index, millis in enumerate([1, now - 5000, now - 4000, now - 3000, now - 2000, now - 1000]):
            db.add(community.DomesticCommunityItem(id=str(index), title="비트코인 가격",
                url=f"https://coinpan.com/free/{index}", source="코인판 자유게시판", published=community._iso(millis),
                published_ms=millis, collected_ms=now, asset_symbols_json='["BTC"]'))
        db.commit()
    result = community.collect_once(now_ms=now)
    assert result["status"] == "error" and result["attempted_sources"] == 4
    with database.get_session() as db:
        rows = db.exec(select(community.DomesticCommunityItem).order_by(community.DomesticCommunityItem.id)).all()
        assert [item.id for item in rows] == ["3", "4", "5"]


def test_no_catalog_means_pending_not_fabricated_source_attempt(monkeypatch):
    def offline(_):
        raise httpx.ConnectTimeout("catalog unavailable")
    monkeypatch.setattr(community.krw, "list_symbols", offline)
    monkeypatch.setattr(community, "fetch_source_listing", lambda *_: pytest.fail("without current catalog"))
    result = community.collect_once()
    assert result["status"] == "pending" and result["attempted_sources"] == 0
    sources = {s["id"]: s for s in result["sources"]}
    assert sources["coinpan_free"]["status"] == "pending"
    assert "last_attempt_ms" not in sources["coinpan_free"]
    with database.get_session() as db:
        state = db.get(community.DomesticCommunityState, community.SCOPE)
        assert state.last_attempt_ms == 0 and state.failures == 0


def test_public_endpoint_accepts_bounded_local_search_and_rejects_arbitrary_sources(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    mock_sources(monkeypatch)
    community.collect_once()
    monkeypatch.setattr(community, "fetch_source_listing", lambda *_: pytest.fail("request-triggered source HTTP"))
    with TestClient(app) as client:
        response = client.get("/api/community/domestic", params={"exchange": "upbit", "q": "BTC",
            "source": "tokenpost_crypto", "limit": 1})
        assert response.status_code == 200
        assert response.json()["items"][0]["source_id"] == "tokenpost_crypto"
        assert client.get("/api/community/domestic?source=https://evil.test").status_code == 400
        assert client.get("/api/community/domestic?limit=101").status_code == 422
        assert client.get("/api/community/domestic", params={"q": "x" * 101}).status_code == 422
