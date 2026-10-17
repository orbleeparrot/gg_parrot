"""Region feeds are canonical, bounded, read-only and isolated on failure."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from app import regional_trends as trends
from app import feed_index
from app.feed_index import FeedAsset
from app.db import get_session
from app.public_news import PublicNewsLease


@pytest.fixture(autouse=True)
def empty_trends():
    with get_session() as db:
        for model in (trends.TrendArticle, trends.TrendSourceState, trends.TrendSummary, FeedAsset):
            db.exec(delete(model))
        db.exec(delete(PublicNewsLease).where(PublicNewsLease.scope.in_([trends.SCOPE, trends.SUMMARY_SCOPE])))
        db.commit()
    trends.clear_caches()
    yield


def article(region="kr", source="blockmedia_rss", title="비트코인 현물 거래 증가", **extra):
    now = int(time.time() * 1000)
    return {"title": title, "source": "블록미디어" if region == "kr" else "CoinDesk",
            "source_id": source, "publisher_region": region, "content_type": "article",
            "url": "https://www.blockmedia.co.kr/archives/100" if region == "kr" else "https://www.coindesk.com/markets/2026/10/17/bitcoin",
            "published": trends.iso(now), "published_ms": now, "asset_symbols": ["BTC"], **extra}


def publish(items):
    with get_session() as db:
        trends.store_items(items, now_ms=int(time.time() * 1000), db=db)
        db.commit()
    trends.clear_caches()


def test_canonical_url_once_across_rediscovery_and_regions():
    first = article(url="https://www.blockmedia.co.kr/archives/100?utm_source=rss")
    publish([first, article()])
    publish([article(title="비트코인 현물 거래 증가", publisher_region="global")])
    with get_session() as db:
        rows = db.exec(select(trends.TrendArticle)).all()
    assert len(rows) == 1
    assert rows[0].region == "kr"  # registered publisher, not incoming language/region


def test_feed_region_and_search_apply_before_limit():
    publish([article(), article("global", "coindesk_rss", asset_symbols=["ETH"],
                               title="이더리움 현물 거래 증가")])
    assert len(trends.get_news("kr")["items"]) == 1
    assert len(trends.get_news("global")["items"]) == 1
    assert len(trends.get_news("kr", q="BTC", assets=["BTC"])["items"]) == 1
    assert trends.get_news("kr", q="ETH", assets=["ETH"])["items"] == []
    assert trends.get_news("global", q="이더리움")["items"][0]["source_id"] == "coindesk_rss"


def test_source_identity_is_not_language_and_unsafe_sources_are_dropped():
    publish([article("global", "coindesk_rss", title="한국 거래소 관련 보도"),
             article(source="unknown"), article(url="http://127.0.0.1/private")])
    assert trends.get_news("kr")["items"] == []
    assert len(trends.get_news("global")["items"]) == 1


def test_no_network_ai_or_mutation_on_http_read(monkeypatch):
    publish([article()])
    def forbidden(*args, **kwargs):
        pytest.fail("public news reader performed collection/AI/mutation")
    monkeypatch.setattr(trends, "collect_once", forbidden)
    monkeypatch.setattr(trends, "prepare_overviews", forbidden)
    monkeypatch.setattr(trends, "collect_source", forbidden)
    assert trends.get_news("kr")["items"]


def test_age_and_total_storage_caps(monkeypatch):
    now = int(time.time() * 1000)
    monkeypatch.setattr(trends, "MAX_ARTICLES", 2)
    publish([article(url=f"https://www.blockmedia.co.kr/archives/{n}", published_ms=now-n*1000)
             for n in range(4)])
    publish([article(url="https://www.blockmedia.co.kr/archives/old", published_ms=now-8*86400000)])
    with get_session() as db:
        assert len(db.exec(select(trends.TrendArticle)).all()) == 2
    assert len(trends.get_news("kr")["items"]) == 2


def test_real_excerpt_is_not_claimed_as_ai_summary_and_internal_fields_hidden():
    publish([article(excerpt="피드에 실제 제공된 짧은 설명", community_body="PRIVATE", author="PRIVATE")])
    item = trends.get_news("kr")["items"][0]
    assert item["excerpt"] == "피드에 실제 제공된 짧은 설명"
    assert item["summary_status"] == "source_excerpt"
    assert "community_body" not in item and "author" not in item


def test_summary_uses_one_existing_budget_for_both_regions_and_is_cached(monkeypatch):
    publish([article(), article("global", "coindesk_rss")])
    calls = []
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(trends, "request_region_overviews", lambda items: calls.append(items) or {
        "kr": "국내 매체가 비트코인 거래 증가를 전했어요.",
        "global": "해외 매체에서도 비트코인 현물 거래를 다뤘어요."})
    assert trends.prepare_overviews()["status"] == "ready"
    assert trends.prepare_overviews()["status"] == "ready"
    assert len(calls) == 1
    assert set(calls[0]) == {"kr", "global"}


def test_summary_failure_not_empty_success_and_retry_is_bounded(monkeypatch):
    publish([article(), article("global", "coindesk_rss")])
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setattr(trends, "request_region_overviews", lambda items: (_ for _ in ()).throw(RuntimeError("429 secret")))
    first = trends.prepare_overviews()
    assert first["status"] == "error"
    assert "secret" not in json.dumps(first)
    assert trends.get_news("kr")["overview"] is None
    assert trends.get_news("kr")["summary"]["status"] == "error"
    assert trends.prepare_overviews()["status"] == "error"  # no immediate repeated call


def test_combined_route_validates_region_exchange_before_source_access(monkeypatch):
    monkeypatch.setattr(trends.market_trends, "get_market_trends", lambda **kw: pytest.fail("source accessed"))
    from app.main import app
    client = TestClient(app)
    for params in ({"region":"kr", "exchange":"binance"},
                   {"region":"global", "exchange":"upbit"}, {"region":"unknown"},
                   {"source":"bogus"}, {"q":"x"*101}, {"min_turnover":"nan"}):
        assert client.get("/api/trends", params=params).status_code == 422


def test_combined_route_isolates_sections_and_applies_search(monkeypatch):
    publish([article()])
    def unavailable(**kw):
        raise RuntimeError("provider secret")
    monkeypatch.setattr(trends.market_trends, "get_market_trends", unavailable)
    monkeypatch.setattr(trends.domestic_community, "get_domestic_community", lambda *a, **kw: {
        "items": [], "collection": {"status":"pending"}})
    from app.main import app
    response = TestClient(app).get("/api/trends?region=kr&exchange=upbit&q=비트코인")
    assert response.status_code == 200
    data = response.json()
    assert data["rankings"]["status"] == "error"
    assert len(data["news"]["items"]) == 1
    assert data["community"]["collection"]["status"] == "pending"
    assert "secret" not in response.text


def test_collect_source_failure_keeps_other_source_and_old_items(monkeypatch):
    publish([article()])
    monkeypatch.setattr(trends, "source_registry", lambda region=None: [
        {"id":"blockmedia_rss", "publisher_region":"kr"},
        {"id":"coindesk_rss", "publisher_region":"global"}])
    def collect(source_id, **kwargs):
        if source_id == "blockmedia_rss":
            raise RuntimeError("not public error")
        return {"items":[article("global", "coindesk_rss")], "source": {
            "id": source_id, "status":"ready", "attempted":True, "cached":False}}
    monkeypatch.setattr(trends, "collect_source", collect)
    monkeypatch.setattr(trends, "prepare_overviews", lambda **kw: {"status":"pending"})
    monkeypatch.setattr(trends, "enrich_items", lambda items: items)
    monkeypatch.setattr(trends, "prepared_listings", lambda: {})
    result = trends.collect_once()
    assert result["status"] == "partial"
    assert trends.get_news("kr")["items"]
    assert trends.get_news("global")["items"]
    assert trends.collect_once()["status"] == "skipped"


def test_rediscovery_does_not_undo_successful_translation():
    raw = article("global", "coindesk_rss", title="Bitcoin spot trading increases")
    publish([raw])
    assert trends.get_news("global")["items"] == []
    publish([{**raw, "title":"비트코인 현물 거래가 증가했어요", "original_title":raw["title"],
              "translation_status":"ready"}])
    publish([raw])
    item = trends.get_news("global")["items"][0]
    assert item["title"] == "비트코인 현물 거래가 증가했어요"
    assert item["translation_status"] == "ready"


def test_two_failed_translation_calls_can_show_original_without_another_call(monkeypatch):
    raw = article("global", "coindesk_rss", title="Bitcoin spot trading increases")
    monkeypatch.setattr(trends.news,"_localize_coin_news_items",lambda *a,**kw:[])
    monkeypatch.setattr(trends.news_ai_budget,"exhausted",lambda kind,titles:set(titles))
    result = trends.enrich_items([raw])
    assert result[0]["translation_status"] == "original_fallback"
    publish(result)
    assert trends.get_news("global")["items"][0]["title"] == raw["title"]


def test_summary_stop_during_provider_call_does_not_write_or_release(monkeypatch):
    import threading
    stop = threading.Event()
    publish([article(),article("global","coindesk_rss")])
    monkeypatch.setenv("OPENAI_API_KEY","test-not-a-key")
    def respond(groups):
        stop.set()
        return {"kr":"국내 기사 요약", "global":"해외 기사 요약"}
    monkeypatch.setattr(trends,"request_region_overviews",respond)
    monkeypatch.setattr(trends,"finish_work",lambda *a,**kw:pytest.fail("release after stop"))
    assert trends.prepare_overviews(stop_event=stop,write_lock=threading.Lock())["status"] == "cancelled"
    with get_session() as db:
        assert db.get(trends.TrendSummary,trends.day_key()) is None


def test_slow_preparation_renews_lease_without_repeating_provider(monkeypatch):
    submitted, renewals = [], []
    class Future:
        attempts = 0
        def result(self,timeout=None):
            self.attempts += 1
            if self.attempts == 1:
                raise trends.FutureTimeout()
            return "actual provider result"
        def done(self):
            return False
    class Pool:
        def __init__(self,**kw):
            pass
        def __enter__(self):
            return self
        def __exit__(self,*args):
            pass
        def submit(self,operation):
            submitted.append(operation)
            return Future()
    monkeypatch.setattr(trends,"ThreadPoolExecutor",Pool)
    monkeypatch.setattr(trends,"renew_work",lambda *args:renewals.append(args))
    assert trends._leased_call("scope","token",lambda:"only once") == "actual provider result"
    assert len(submitted) == 1 and renewals == [("scope","token")]


def test_regional_summary_and_legacy_share_total_two_provider_attempts(monkeypatch):
    from types import SimpleNamespace
    provider_calls = []
    monkeypatch.setenv("OPENAI_API_KEY","test-key-for-regional-budget")
    monkeypatch.setattr(trends.news,"_market_summary_budget",("",0))
    def create(**kw):
        provider_calls.append(kw)
        raise RuntimeError("simulated provider failure")
    monkeypatch.setattr(trends,"get_ai_client",lambda:SimpleNamespace(messages=SimpleNamespace(create=create)))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            trends.request_region_overviews({"kr":[],"global":[]})
    with pytest.raises(ValueError,match="daily_summary_budget_exhausted"):
        trends.request_region_overviews({"kr":[],"global":[]})
    assert len(provider_calls) == 2
    assert trends.news._reserve_market_summary_call() is False


def test_rank_display_filters_do_not_change_news_asset_resolution(monkeypatch):
    publish([article(title="새 프로젝트 거래 증가", asset_symbols=["XYZ"])])
    full = {"exchange":"upbit","coins":[{"base":"XYZ","symbol":"KRW-XYZ","exchange":"upbit",
            "korean_name":"새로운코인","english_name":"Newcoin","quote_volume":10,"change_pct":-1}],
            "coverage":{},"status":"ready"}
    monkeypatch.setattr(trends.market_trends,"get_market_trends",lambda **kw:full)
    monkeypatch.setattr(trends.domestic_community,"get_domestic_community",lambda *a,**kw:{"items":[]})
    from app.main import app
    result = TestClient(app).get("/api/trends?region=kr&q=새로운코인&min_turnover=100&direction=up").json()
    assert result["rankings"]["coins"] == []
    assert len(result["news"]["items"]) == 1


def test_new_feature_keeps_legacy_market_summary_from_duplicate_call(monkeypatch):
    from app import public_news
    monkeypatch.setenv("REGIONAL_TRENDS_ENABLED","true")
    monkeypatch.setattr(news := trends.news,"_load_durable_market_summary",lambda day:None)
    monkeypatch.setattr(news,"_fetch_public_news_payload",lambda **kw:{"items":[],"as_of":trends.day_key()})
    monkeypatch.setattr(news,"_localize_news_payload",lambda payload,**kw:payload)
    monkeypatch.setattr(news,"_summarize",lambda *a,**kw:pytest.fail("second summary workflow"))
    monkeypatch.setattr(trends,"combined_market_overview",lambda:"저장된 국내·해외 요약")
    monkeypatch.setattr(news,"_store_durable_market_summary",lambda *a,**kw:None)
    result = public_news.collect_market()
    assert result["overview"] == "저장된 국내·해외 요약"


def test_changed_source_title_does_not_keep_a_stale_translation():
    raw = article("global","coindesk_rss",title="Bitcoin spot trading increases")
    publish([{**raw,"title":"비트코인 현물 거래 증가","original_title":raw["title"],"translation_status":"ready"}])
    publish([{**raw,"title":"Ethereum spot trading decreases","asset_symbols":["ETH"]}])
    assert trends.get_news("global")["items"] == []
    assert trends.get_news("global")["translation"]["pending_count"] == 1


def test_original_fallback_stays_visible_during_same_source_rediscovery():
    raw = article("global","coindesk_rss",title="Bitcoin spot trading increases")
    publish([{**raw,"original_title":raw["title"],"translation_status":"original_fallback"}])
    publish([raw])
    assert trends.get_news("global")["items"][0]["translation_status"] == "original_fallback"


def test_provider_pause_does_not_consume_a_summary_attempt(monkeypatch):
    from app.ai_runtime import AiBusyError
    from app import ai_provider_health
    monkeypatch.setenv("OPENAI_API_KEY","test-regional-provider-paused")
    monkeypatch.setattr(ai_provider_health,"ensure_available",lambda:(_ for _ in ()).throw(AiBusyError("paused")))
    monkeypatch.setattr(trends.news,"_reserve_market_summary_call",lambda:pytest.fail("budget burned while paused"))
    with pytest.raises(AiBusyError):
        trends.request_region_overviews({"kr":[{"title":"국내"}],"global":[{"title":"해외"}]})


def test_news_page_reads_one_page_in_sql_and_counts_only_on_the_first_page():
    # 2026-10-18: 예전엔 최근 7일 기사(최대 1,000건 · 4MB)를 다 읽고 잘랐다 — 이제 DB 가 limit+1 행만 준다.
    now = int(time.time() * 1000)
    publish([article(title=f"비트코인 기사 {n}", url=f"https://www.blockmedia.co.kr/archives/{300 + n}",
                     published=trends.iso(now - n * 1000), published_ms=now - n * 1000) for n in range(7)])
    statements = []
    with get_session() as db:
        engine = db.get_bind()
    def capture(_conn, _cursor, sql, *_args):
        if "FROM trendarticle" in sql and "count(" not in sql.lower() and sql.lstrip().upper().startswith("SELECT"):
            statements.append(sql)
    from sqlalchemy import event
    event.listen(engine, "before_cursor_execute", capture)
    try:
        first = trends.get_news("kr", limit=3)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [item["title"] for item in first["items"]] == ["비트코인 기사 0", "비트코인 기사 1", "비트코인 기사 2"]
    assert first["total"] == 7 and first["next_cursor"]
    assert len(statements) == 1 and "LIMIT" in statements[0].upper()
    second = trends.get_news("kr", limit=3, cursor=feed_index.decode_cursor(first["next_cursor"]))
    third = trends.get_news("kr", limit=3, cursor=feed_index.decode_cursor(second["next_cursor"]))
    assert [item["title"] for item in second["items"] + third["items"]] == [f"비트코인 기사 {n}" for n in range(3, 7)]
    assert second["total"] is None and third["next_cursor"] is None


def test_pending_translation_is_hidden_and_old_rows_are_backfilled():
    publish([article(title="Bitcoin ETF inflows rise", url="https://www.blockmedia.co.kr/archives/401",
                     translation_status="pending")])
    assert trends.get_news("kr")["items"] == []
    assert trends.get_news("kr")["translation"]["pending_count"] == 1
    # 개편 전에 저장된 행 — 검색 칸 · 코인 연결이 비어 있다. 수집기 첫 회차가 채운다.
    with get_session() as db:
        db.add(trends.TrendArticle(id="legacy1", region="kr", source_id="blockmedia_rss", published_ms=int(time.time() * 1000),
               collected_ms=0, item_json=json.dumps(article(title="이더리움 옛 기사", asset_symbols=["ETH"]), ensure_ascii=False)))
        db.commit()
        assert trends.reconcile_projection(db) == 1
        db.commit()
        assert trends.reconcile_projection(db) == 0
    trends.clear_caches()
    found = trends.get_news("kr", q="이더리움")
    assert [item["title"] for item in found["items"]] == ["이더리움 옛 기사"]
    assert [item["title"] for item in trends.get_news("kr", selected="ETH")["items"]] == ["이더리움 옛 기사"]


def square_row(identifier, *, scope="BTC", categories=("BTC",), last_seen=None, title="비트코인 커뮤니티 글"):
    from app.agent_features.position_news.articles import NewsArticle
    now = int(time.time() * 1000)
    item = {"title": title, "source": "Binance Square", "content_type": "community",
            "community_post_id": identifier, "url": f"https://www.binance.com/en/square/post/{identifier}",
            "published": trends.iso(now), "categories": list(categories), "community_summary": "요약",
            "community_summary_status": "ready", "community_body": "PRIVATE"}
    with get_session() as db:
        db.merge(NewsArticle(asset_symbol=scope, article_id=f"{scope}-{identifier}", revision=0, ready=True,
                             item_json=json.dumps(item, ensure_ascii=False), first_seen_ms=now,
                             last_seen_ms=now if last_seen is None else last_seen))
        db.commit()


def test_square_posts_are_copied_once_then_incrementally_and_read_in_pages(monkeypatch):
    # 2026-10-18: Square 를 읽을 때마다 newsarticle 전체를 JSON 으로 풀던 것(첫 쪽 최대 12.8초) → 수집기가 옮겨 둔다.
    from app.agent_features.position_news.articles import NewsArticle
    from sqlmodel import delete as sql_delete
    with get_session() as db:
        db.exec(sql_delete(NewsArticle))
        db.commit()
    now = int(time.time() * 1000)
    square_row("101", scope="BTC", categories=("BTC",), last_seen=now - 5000)
    square_row("101", scope="ETH", categories=("ETH",), last_seen=now - 4000)  # 같은 글의 다른 코인 범위
    square_row("102", last_seen=now - 3000, title="두 번째 글")
    assert trends.sync_square()["moved"] == 2
    page = trends.get_global_community(limit=1)
    assert page["total"] == 2 and page["next_cursor"] and page["mentions"] == {"BTC": 2, "ETH": 1}
    rest = trends.get_global_community(limit=1, cursor=feed_index.decode_cursor(page["next_cursor"]))
    assert {item["community_post_id"] for item in page["items"] + rest["items"]} == {"101", "102"}
    assert "PRIVATE" not in json.dumps(page) + json.dumps(rest)
    merged = next(item for item in page["items"] + rest["items"] if item["community_post_id"] == "101")
    assert merged["asset_symbols"] == ["BTC", "ETH"]
    # 이어 읽기: 첫 회 뒤에 바뀐 행만 — 표 전체를 다시 훑지 않는다.
    loads = []
    import app.prepared_square_feed as square
    original = square.load_changed
    monkeypatch.setattr(square, "load_changed", lambda since=None, **kw: loads.append(since) or original(since, **kw))
    square_row("103", last_seen=int(time.time() * 1000) + 1000, title="새 글")
    trends.sync_square()
    assert loads and loads[0] is not None
    trends.clear_caches()
    assert trends.get_global_community(limit=20)["total"] == 3
    assert [item["title"] for item in trends.get_global_community(q="새 글")["items"]] == ["새 글"]


def test_each_feed_has_its_own_storage_cap(monkeypatch):
    monkeypatch.setattr(trends, "MAX_ARTICLES", 2)
    now = int(time.time() * 1000)
    publish([article(url=f"https://www.blockmedia.co.kr/archives/{500 + n}", published_ms=now - n * 1000) for n in range(2)])
    with get_session() as db:
        trends.store_square_items([{"id": f"sq{n}", "title": f"스퀘어 {n}", "published_ms": now, "asset_symbols": ["BTC"],
                                    "published": trends.iso(now), "source": "Binance Square"} for n in range(5)],
                                  now_ms=now, db=db)
        db.commit()
    trends.clear_caches()
    assert trends.get_news("kr")["total"] == 2  # Square 가 많아도 뉴스를 밀어내지 않는다
    assert trends.get_global_community()["total"] == 2


def test_source_catalog_exposes_news_and_community_without_database_or_fetch(monkeypatch):
    monkeypatch.setattr(trends,"get_session",lambda:pytest.fail("DB on static sources"))
    monkeypatch.setattr(trends,"collect_source",lambda *a,**kw:pytest.fail("fetch on static sources"))
    from app.main import app
    client = TestClient(app)
    domestic = client.get("/api/trends/sources?region=kr").json()
    ids = {row["id"] for row in domestic["sources"]}
    assert {"blockmedia_rss","tokenpost_crypto","tokenpost_chart","coinpan_free","dcinside_bitcoin"} <= ids
    assert domestic["exchanges"] == ["upbit","bithumb"]
    global_sources = client.get("/api/trends/sources?region=global").json()
    assert "binance_square" in {row["id"] for row in global_sources["sources"]}
    assert global_sources["quote_currency"] == "USDT"


def test_paged_sections_skip_rankings_and_slice_lists(monkeypatch):
    now = int(time.time() * 1000)
    publish([article(title=f"비트코인 기사 {n}", url=f"https://www.blockmedia.co.kr/archives/{200 + n}",
                     published=trends.iso(now - n * 1000), published_ms=now - n * 1000) for n in range(5)])
    monkeypatch.setattr(trends.market_trends, "get_market_trends", lambda **kw: pytest.fail("rankings not requested"))
    from app.main import app
    client = TestClient(app)
    data = client.get("/api/trends", params={"region": "kr", "sections": "news", "limit": 2, "offset": 2}).json()
    assert data["rankings"] is None and data["community"] is None
    assert [item["title"] for item in data["news"]["items"]] == ["비트코인 기사 2", "비트코인 기사 3"]
    assert data["news"]["total"] == 5 and data["news"]["offset"] == 2 and data["offset"] == 2
    for bad in ("", "rankings,secrets", "everything"):
        assert client.get("/api/trends", params={"region": "kr", "sections": bad}).status_code == 422
    # 다음 쪽은 next_cursor 로 — 화면이 쓰는 방식.
    first = client.get("/api/trends", params={"region": "kr", "sections": "news", "limit": 2}).json()["news"]
    second = client.get("/api/trends", params={"region": "kr", "sections": "news", "limit": 2,
                                               "cursor": first["next_cursor"]}).json()["news"]
    assert [item["title"] for item in first["items"] + second["items"]] == [f"비트코인 기사 {n}" for n in range(4)]
    for bad in ("x", "1.a b", "-1.abc", "1." + "a" * 200):
        assert client.get("/api/trends", params={"region": "kr", "sections": "news", "cursor": bad}).status_code == 422


def test_first_pages_are_served_from_cache_without_new_reads(monkeypatch):
    publish([article()])
    clock = [1000.0]
    monkeypatch.setattr(trends._pages, "clock", lambda: clock[0])
    monkeypatch.setattr(trends._meta, "clock", lambda: clock[0])
    trends.warm_readers()
    reads = []
    original = trends._read_page
    monkeypatch.setattr(trends, "_read_page", lambda *a, **k: reads.append(a) or original(*a, **k))
    import app.cache_runtime as cache_runtime
    monkeypatch.setattr(cache_runtime, "_submit", lambda job: True)  # 뒤에서 새로 만드는 일은 멈춰 둔다
    clock[0] += 5 * 60
    page = trends.get_news("kr", limit=20)
    assert reads == [] and [item["title"] for item in page["items"]] == ["비트코인 현물 거래 증가"]
    assert page["stale"] is False
    trends.warm_readers()  # 캐시에 있으면 데우기는 DB 를 읽지 않는다
    assert reads == []


def test_reader_list_older_than_two_minutes_is_labelled_stale(monkeypatch):
    publish([article()])
    trends.warm_readers()
    real = time.time()
    monkeypatch.setattr(trends.time, "time", lambda: real + 3 * 60)
    assert trends.get_news("kr", limit=20)["stale"] is True


def test_collection_loop_warms_both_region_readers(monkeypatch):
    calls = []
    monkeypatch.setattr(trends, "collect_once", lambda **_: None)
    monkeypatch.setattr(trends, "warm_readers", lambda: calls.append("warm"))
    runtime = trends._Runtime()
    monkeypatch.setattr(runtime.stop, "wait", lambda _seconds: runtime.stop.set())
    runtime.run()
    assert calls == ["warm"]


def test_storing_many_square_posts_uses_a_bounded_number_of_statements():
    # 2026-10-18 운영: 946개를 글마다 읽고 · 지우고 · 넣어 몇 분이 걸려(왕복 약 80ms) 수집기 임대(2분)가 끊겼다.
    from sqlalchemy import event
    now = int(time.time() * 1000)
    items = [{"id": f"sq{n:04d}", "title": f"스퀘어 {n}", "published_ms": now - n, "asset_symbols": ["BTC", "ETH"],
              "published": trends.iso(now), "source": "Binance Square"} for n in range(200)]
    statements = []
    with get_session() as db:
        engine = db.get_bind()
        def count(*_args):
            statements.append(1)
        event.listen(engine, "before_cursor_execute", count)
        try:
            assert trends.store_square_items(items, now_ms=now, db=db) == 200
            db.commit()
        finally:
            event.remove(engine, "before_cursor_execute", count)
    assert len(statements) < 30, len(statements)  # 글 수와 상관없이 몇 번
    trends.clear_caches()
    assert trends.get_global_community(limit=5)["total"] == 200
    assert trends.get_global_community(limit=5, selected="ETH")["total"] == 200


def test_square_watermark_is_committed_with_each_batch_even_if_the_lease_breaks_later(monkeypatch):
    import app.prepared_square_feed as square
    now = int(time.time() * 1000)
    batches = iter([
        {"items": [{"id": "sqa", "title": "첫 묶음", "published_ms": now, "asset_symbols": ["BTC"],
                    "published": trends.iso(now), "source": "Binance Square"}],
         "max_last_seen_ms": now - 500, "truncated": True},
        {"items": [], "max_last_seen_ms": None, "truncated": False},
    ])
    monkeypatch.setattr(square, "load_changed", lambda since=None, **kw: next(batches))
    monkeypatch.setattr(trends, "_leased_call", lambda scope, token, operation, **kw: operation())
    monkeypatch.setattr(trends, "_fence", lambda db, token: None)
    renewals = []
    def renew(scope, token):
        renewals.append(scope)
        if len(renewals) > 1:
            raise RuntimeError("public news preparation lease lost")
    monkeypatch.setattr(trends, "renew_work", renew)
    with get_session() as db:  # 이미 첫 채우기를 끝낸 상태에서 이어 읽기
        db.add(trends.TrendSourceState(source_id="binance_square", region="global", status="ready",
                                       source_json=json.dumps({"watermark_ms": now - 60_000})))
        db.commit()
    with pytest.raises(RuntimeError, match="lease lost"):
        trends.sync_square(token="t")
    with get_session() as db:
        state = json.loads(db.get(trends.TrendSourceState, "binance_square").source_json)
        stored = db.get(trends.TrendArticle, "sqa")
    assert stored is not None and state["watermark_ms"] == now - 500  # 첫 묶음의 글과 자리가 함께 남았다
