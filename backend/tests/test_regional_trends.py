"""Region feeds are canonical, bounded, read-only and isolated on failure."""
import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import delete, select

from app import regional_trends as trends
from app.db import get_session
from app.public_news import PublicNewsLease


@pytest.fixture(autouse=True)
def empty_trends():
    with get_session() as db:
        for model in (trends.TrendArticle, trends.TrendSourceState, trends.TrendSummary):
            db.exec(delete(model))
        db.exec(delete(PublicNewsLease).where(PublicNewsLease.scope.in_([trends.SCOPE, trends.SUMMARY_SCOPE])))
        db.commit()
    trends._responses.clear()
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
    trends._responses.clear()


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


def test_read_candidate_cap_is_before_search_and_declared(monkeypatch):
    monkeypatch.setattr(trends,"MAX_READ_CANDIDATES",2)
    publish([article(url=f"https://www.blockmedia.co.kr/archives/{n}") for n in range(4)])
    result = trends.get_news("kr")
    assert result["coverage"]["candidate_limit"] == 2
    assert result["coverage"]["truncated"] is True
    assert len(result["items"]) == 2


def test_snapshot_byte_cap_prevents_oversized_cache_refetches(monkeypatch):
    monkeypatch.setattr(trends,"MAX_SNAPSHOT_BYTES",1000)
    publish([article(url=f"https://www.blockmedia.co.kr/archives/{n}",excerpt="가"*300) for n in range(4)])
    result = trends.get_news("kr")
    assert result["coverage"]["byte_limited"] is True
    assert result["coverage"]["truncated"] is True
    assert trends._responses.statistics()["oversized"] == 0


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
