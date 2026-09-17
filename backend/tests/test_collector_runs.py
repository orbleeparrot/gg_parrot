"""수집 엔진 실행 기록 — 실행 행·소스 누적·정리, 엔진 상태 판정, 각 흐름의 훅이 기록을 남기는지(외부 I/O 없음)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlmodel import select

from app import collector_runs, news, public_news
from app.agent_features.position_news import articles, collector
from app.db import CollectorRun, CollectorSourceDaily, get_session

_KST = timezone(timedelta(hours=9))
# 2026-09-17 10:00 KST — 오늘/시간대 계산이 실제 시각과 무관하게 고정된다.
NOW_MS = int(datetime(2026, 9, 17, 10, 0, tzinfo=_KST).timestamp() * 1000)
TODAY = "2026-09-17"


@pytest.fixture(autouse=True)
def clean_tables(monkeypatch):
    # 다른 테스트의 flow 호출도 같은 표에 쓴다 — 매번 빈 표에서 시작하고, 정리는 명시적으로 켤 때만 돈다.
    with get_session() as db:
        db.exec(delete(CollectorRun))
        db.exec(delete(CollectorSourceDaily))
        db.exec(delete(articles.NewsMaintenanceLease).where(articles.NewsMaintenanceLease.name == articles.ENRICHMENT_STALL_LEASE))
        db.commit()
    monkeypatch.setattr(collector_runs, "_last_warn_monotonic", None)
    monkeypatch.setattr(collector_runs, "_last_prune_ms", int(time.time() * 1000))
    yield


def _runs(engine=None) -> list[CollectorRun]:
    with get_session() as db:
        statement = select(CollectorRun).order_by(CollectorRun.id)
        if engine:
            statement = statement.where(CollectorRun.engine == engine)
        return db.exec(statement).all()


def _sources(engine) -> dict[str, CollectorSourceDaily]:
    with get_session() as db:
        return {row.source: row for row in db.exec(select(CollectorSourceDaily).where(CollectorSourceDaily.engine == engine)).all()}


def _run_row(engine, *, age_s, status="ok", error="", items=0, failures=0, targets=0):
    finished = NOW_MS - int(age_s * 1000)
    return CollectorRun(engine=engine, day_kst=collector_runs.day_kst(finished), started_ms=finished - 1000, finished_ms=finished,
                        status=status, error=error, items=items, failures=failures, targets=targets)


# --- 기록 ------------------------------------------------------------------------
def test_record_run_compacts_summary_and_trims_error():
    summary = {"ticker_count": 2, "items": [{"asset_symbol": "BTC"}] * 3, "browser_failed_tickers": ["BTC"],
               "configuration": {"collector_mode": "rss"}}
    row = collector_runs.record_run("position_news", started_ms=NOW_MS - 5000, finished_ms=NOW_MS, status="degraded",
                                    targets=2, items=3, failures=1, error="x" * 400 + "\nstack line", summary=summary)
    assert row.id and (row.day_kst, row.status, row.targets, row.items, row.failures) == (TODAY, "degraded", 2, 3, 1)
    stored = json.loads(row.summary_json)
    assert "items" not in stored and stored["items_count"] == 3
    assert stored["browser_failed_tickers"] == ["BTC"] and stored["configuration"] == {"collector_mode": "rss"}
    assert row.error == "x" * 300, "여러 줄 오류는 첫 줄만, 300자까지"


def test_summary_json_never_exceeds_eight_kilobytes():
    huge = {"ticker_count": 1, "configuration": {f"key{n}": "v" * 100 for n in range(200)}}
    row = collector_runs.record_run("position_news", started_ms=NOW_MS, finished_ms=NOW_MS, summary=huge)
    assert len(row.summary_json.encode("utf-8")) <= collector_runs.SUMMARY_MAX_BYTES
    assert json.loads(row.summary_json) == {"ticker_count": 1}, "중첩 설정이 크면 스칼라만 남긴다"


def test_error_text_hides_key_like_values():
    row = collector_runs.record_run("coindesk_probe", started_ms=NOW_MS, finished_ms=NOW_MS, status="error",
                                    error="HTTP 401 api_key=abcd1234 rejected")
    assert "abcd1234" not in row.error and "[redacted]" in row.error


def test_bump_source_accumulates_and_keeps_last_error_and_latest_success():
    assert collector_runs.bump_source("position_news", "google", calls=1, items=5, targets=1, success_ms=NOW_MS - 1000, now_ms=NOW_MS)
    assert collector_runs.bump_source("position_news", "google", calls=1, failures=1, targets=1, error="HTTP 503", now_ms=NOW_MS + 1000)
    # 더 이른 성공 시각은 마지막 성공을 뒤로 돌리지 않고, 빈 오류는 마지막 오류를 지우지 않는다.
    assert collector_runs.bump_source("position_news", "google", calls=1, items=2, targets=1, success_ms=NOW_MS - 5000, now_ms=NOW_MS + 2000)
    row = _sources("position_news")["google"]
    assert (row.day_kst, row.calls, row.items, row.failures, row.targets) == (TODAY, 3, 7, 1, 3)
    assert row.last_error == "HTTP 503" and row.last_success_ms == NOW_MS - 1000 and row.updated_ms == NOW_MS + 2000


def test_record_run_prunes_rows_older_than_retention_once_per_hour(monkeypatch):
    old = NOW_MS - 20 * 86_400_000
    with get_session() as db:
        db.add(CollectorRun(engine="whale_activity", day_kst="2026-08-28", started_ms=old, finished_ms=old))
        db.commit()
    monkeypatch.setattr(collector_runs, "_last_prune_ms", 0)
    collector_runs.record_run("whale_activity", started_ms=NOW_MS, finished_ms=NOW_MS)
    assert [row.started_ms for row in _runs("whale_activity")] == [NOW_MS]
    with get_session() as db:
        db.add(CollectorRun(engine="whale_activity", day_kst="2026-08-28", started_ms=old, finished_ms=old))
        db.commit()
    collector_runs.record_run("whale_activity", started_ms=NOW_MS, finished_ms=NOW_MS)
    assert sorted(row.started_ms for row in _runs("whale_activity")) == [old, NOW_MS, NOW_MS], "한 시간 안에는 다시 지우지 않는다"


def test_recording_never_raises_when_the_database_is_unavailable(monkeypatch, caplog):
    def broken():
        raise RuntimeError("db down")
    monkeypatch.setattr(collector_runs, "_session", broken)
    with caplog.at_level(logging.WARNING, logger="app.collector_runs"):
        assert collector_runs.record_run("position_news", started_ms=NOW_MS, finished_ms=NOW_MS) is None
        assert collector_runs.bump_source("position_news", "google", calls=1) is False
        collector_runs.bump_news_sources([{"name": "google_news_rss", "status": "ready"}])
        collector_runs.bump_source_results("whale_activity", [{"market": "spot", "status": "empty"}],
                                           source_key_name="market", items_key="large_trade_count", subject_key="symbol")
        with collector_runs.RunRecorder("whale_activity") as run:
            run.report(targets=1)
    assert sum("[collector_runs]" in record.message for record in caplog.records) == 1, "경고는 1분에 한 번만"


def test_run_recorder_records_error_and_reraises():
    with pytest.raises(ValueError):
        with collector_runs.RunRecorder("onchain_holders") as run:
            run.report({"due_coin_count": 1}, targets=1)
            raise ValueError("Blockscout 429")
    (row,) = _runs("onchain_holders")
    assert (row.status, row.error, row.targets) == ("error", "ValueError: Blockscout 429", 1)
    assert row.finished_ms >= row.started_ms > 0


def test_run_recorder_disabled_writes_nothing():
    with collector_runs.RunRecorder("article_enrichment", enabled=False) as run:
        run.targets = 3
    assert _runs() == []


# --- 소스 접기 ------------------------------------------------------------------
def test_bump_source_results_folds_rows_per_source_and_skips_unattempted():
    rows = [
        {"symbol": "BTCUSDT", "market": "spot", "status": "empty", "large_trade_count": 0},
        {"symbol": "ETHUSDT", "market": "spot", "status": "ready", "large_trade_count": 4},
        {"symbol": "SOLUSDT", "market": "futures", "status": "error", "error_code": "rate_limited", "http_status": 429},
        {"symbol": "XRPUSDT", "market": "futures", "status": "skipped"},
    ]
    collector_runs.bump_source_results("whale_activity", rows, source_key_name="market", items_key="large_trade_count",
                                       subject_key="symbol", now_ms=NOW_MS)
    sources = _sources("whale_activity")
    assert (sources["spot"].calls, sources["spot"].items, sources["spot"].failures, sources["spot"].last_success_ms) == (2, 4, 0, NOW_MS)
    assert (sources["futures"].calls, sources["futures"].failures, sources["futures"].last_error) == (1, 1, "rate_limited 429 (SOLUSDT)")
    assert sources["futures"].last_success_ms == 0


def test_bump_news_sources_maps_names_folds_browser_pages_and_ignores_cache_hits():
    sources = [
        {"name": "google_news_rss", "status": "ready", "item_count": 5},
        {"name": "coindesk_rss", "status": "error", "item_count": 0},
        {"name": "coindesk_news_api", "status": "error", "error": "call_budget_exhausted", "cached": False, "item_count": 0},
        {"name": "coindesk_news_api", "status": "ready", "cached": True, "item_count": 9},
        {"name": "binance_square", "status": "disabled"},
        {"name": "decrypt_rss", "status": "ready", "item_count": 2},
        {"name": "coindesk_markets", "source_type": "coindesk_index_playwright", "status": "ready", "item_count": 3},
        {"name": "coindesk_search", "source_type": "coindesk_search_playwright", "status": "error", "error": "TimeoutError",
         "message": "Browser batch deadline (90s) exceeded"},
        {"name": "decrypt_index", "source_type": "decrypt_index_playwright", "status": "ready", "cached": True, "item_count": 7},
    ]
    collector_runs.bump_news_sources(sources, targets=1, now_ms=NOW_MS)
    rows = _sources("position_news")
    assert set(rows) == {"google", "coindesk", "coindesk_api", "decrypt_rss", "browser"}
    assert (rows["google"].calls, rows["google"].items, rows["google"].targets, rows["google"].last_success_ms) == (1, 5, 1, NOW_MS)
    assert (rows["coindesk"].calls, rows["coindesk"].failures) == (1, 1)
    assert (rows["coindesk_api"].calls, rows["coindesk_api"].failures, rows["coindesk_api"].last_error) == (1, 1, "call_budget_exhausted")
    assert (rows["browser"].calls, rows["browser"].items, rows["browser"].failures, rows["browser"].targets) == (2, 3, 1, 1)
    assert rows["browser"].last_error == "TimeoutError Browser batch deadline (90s) exceeded"


# --- 보고 ------------------------------------------------------------------------
def test_engines_report_statuses_totals_hourly_and_source_labels():
    with get_session() as db:
        db.add_all([
            _run_row("position_news", age_s=30, items=10, failures=1, targets=5),
            _run_row("position_news", age_s=5400, status="error", error="Google RSS 503", failures=3, targets=5),  # 08:30 KST
            _run_row("whale_activity", age_s=100),           # 30초 주기 × 3 = 90초를 넘겼다
            _run_row("onchain_holders", age_s=10, status="error", error="blockscout 429"),
            _run_row("public_news", age_s=800),              # 5분 × 3 = 15분 안
            _run_row("coindesk_probe", age_s=3 * 86_400),    # 수동 실행은 지연을 보지 않는다
        ])
        db.commit()
        collector_runs.bump_source("position_news", "browser", calls=8, items=3, failures=2, targets=1, error="TimeoutError", now_ms=NOW_MS, db=db)
        collector_runs.bump_source("position_news", "google", calls=4, items=20, targets=4, success_ms=NOW_MS, now_ms=NOW_MS, db=db)
        collector_runs.bump_source("position_news", "zeta_feed", calls=1, now_ms=NOW_MS, db=db)
        collector_runs.bump_source("whale_activity", "spot", calls=1, now_ms=NOW_MS, db=db)
        collector_runs.bump_source("position_news", "google", calls=9, now_ms=NOW_MS - 86_400_000, db=db)  # 어제
        report = collector_runs.engines_report(db, now_ms=NOW_MS)

    engines = report["engines"]
    assert [row["engine"] for row in engines] == [engine for engine, *_ in collector_runs.ENGINES]
    assert [row["label"] for row in engines] == ["종목 뉴스 수집", "고래 거래 수집", "온체인 보유 수집", "공개 뉴스 (코인동향)",
                                                 "기사 보강 (AI 요약)", "CoinDesk 소스 점검"]
    assert engines[3]["mode"] == "웹 · MARKET + 종목별" and engines[5]["mode"] == "Prefect · 수동"
    by = {row["engine"]: row for row in engines}
    assert (by["position_news"]["status"], by["position_news"]["status_label"]) == ("ok", "정상")
    assert (by["position_news"]["runs_today"], by["position_news"]["targets_today"], by["position_news"]["items_today"],
            by["position_news"]["failures_today"]) == (2, 10, 10, 4)
    assert by["position_news"]["last_error"] == "Google RSS 503" and by["position_news"]["last_run_ms"] == NOW_MS - 30_000
    assert (by["whale_activity"]["status"], by["whale_activity"]["status_label"]) == ("delayed", "지연")
    assert (by["onchain_holders"]["status"], by["onchain_holders"]["last_error"]) == ("error", "blockscout 429")
    assert by["public_news"]["status"] == "ok"
    assert (by["article_enrichment"]["status"], by["article_enrichment"]["status_label"], by["article_enrichment"]["last_run_ms"]) == ("idle", "대기", 0)
    assert by["coindesk_probe"]["status"] == "ok" and by["coindesk_probe"]["runs_today"] == 0

    hourly = report["hourly"]
    assert [row["hour"] for row in hourly] == list(range(24))
    assert hourly[9] == {"hour": 9, "items": 10, "failures": 1} and hourly[8] == {"hour": 8, "items": 0, "failures": 3}
    assert sum(row["items"] for row in hourly) == 10

    sources = report["sources"]
    assert [(row["source"], row["label"]) for row in sources] == [
        ("google", "Google News RSS"), ("browser", "브라우저 보강 (Playwright)"), ("zeta_feed", "zeta_feed")]
    browser = sources[1]
    assert (browser["calls"], browser["items"], browser["failures"], browser["failure_pct"], browser["last_error"]) == (8, 3, 2, 25.0, "TimeoutError")
    assert (sources[0]["calls"], sources[0]["failure_pct"], sources[0]["last_success_ms"]) == (4, 0.0, NOW_MS), "어제 행은 빠진다"


def test_position_news_delay_threshold_allows_one_long_cycle():
    with get_session() as db:
        db.add(_run_row("position_news", age_s=300))
        db.commit()
        assert collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][0]["status"] == "ok", "최대 사이클(240초+30초)+주기 안"
        db.add(_run_row("position_news", age_s=200))
        db.commit()
        db.exec(delete(CollectorRun))
        db.add(_run_row("position_news", age_s=400))
        db.commit()
        assert collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][0]["status"] == "delayed"


def test_enrichment_status_stalled_then_delayed_only_with_due_work(monkeypatch):
    with get_session() as db:
        db.add(_run_row("article_enrichment", age_s=20))
        db.commit()
        articles.record_enrichment_pass(False, now_ms=NOW_MS - 18_000, db=db)  # 60초 정체 → 42초 남음
        row = collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][4]
        assert (row["status"], row["status_label"]) == ("stalled", "정체 쉼 · 42초 뒤 탐침")
        articles.record_enrichment_pass(True, now_ms=NOW_MS, db=db)
        assert collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][4]["status"] == "ok"

        db.exec(delete(CollectorRun))
        db.add(_run_row("article_enrichment", age_s=1000))
        db.commit()
        monkeypatch.setattr(collector_runs, "_enrichment_has_due_work", lambda _db, _ms: False)
        assert collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][4]["status"] == "ok", "보강할 기사가 없어 안 돈 것은 지연이 아니다"
        monkeypatch.setattr(collector_runs, "_enrichment_has_due_work", lambda _db, _ms: True)
        assert collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][4]["status"] == "delayed"


def test_enrichment_due_work_reads_pending_articles():
    with get_session() as db:
        db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == "CRTEST"))
        db.commit()
    try:
        articles.upsert_articles("CRTEST", [{"title": "Collector run test headline", "source": "x", "url": "https://example.test/cr"}],
                                 now_ms=NOW_MS)
        with get_session() as db:
            assert collector_runs._enrichment_has_due_work(db, NOW_MS) is True
    finally:
        with get_session() as db:
            db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == "CRTEST"))
            db.exec(delete(articles.NewsArticleFeed).where(articles.NewsArticleFeed.asset_symbol == "CRTEST"))
            db.commit()


# --- 훅: news.py 소스 로더 ------------------------------------------------------------
def _article(title, *, url="https://news.test/current"):
    return {"title": title, "source": "CoinDesk", "url": url, "published": datetime.now(timezone.utc).isoformat()}


@pytest.fixture
def no_snapshots(monkeypatch):
    monkeypatch.setattr(news, "_load_latest_coin_snapshot", lambda _: None)


def test_news_envelope_records_sources_once_at_the_final_call(no_snapshots):
    fetched = {
        "google": ({"items": [_article("Bitcoin rallies past resistance")], "sources": [{"name": "google_news_rss", "status": "ready"}]}, True),
        "coindesk": ([], False),
        "coindesk_api": {"items": [], "source": {"name": "coindesk_news_api", "status": "error", "error": "call_budget_exhausted", "cached": False}},
    }
    news._collected_news_envelope("BTC", "비트코인", "q", fetched, partial=True)
    assert _sources("position_news") == {}, "진행 중 호출은 세지 않는다"
    env = news._collected_news_envelope("BTC", "비트코인", "q", fetched)
    rows = _sources("position_news")
    google = next(source for source in env["sources"] if source["name"] == "google_news_rss")
    assert (rows["google"].calls, rows["google"].items, rows["google"].failures, rows["google"].targets) == (1, google["item_count"], 0, 1)
    assert (rows["coindesk"].calls, rows["coindesk"].failures) == (1, 1)
    assert (rows["coindesk_api"].failures, rows["coindesk_api"].last_error) == (1, "call_budget_exhausted")


def test_news_envelope_counts_failures_when_every_source_failed(no_snapshots):
    fetched = {"google": ({"items": [], "sources": [{"name": "google_news_rss", "status": "error"}]}, False), "coindesk": ([], False)}
    with pytest.raises(news.NewsFetchError) as raised:
        news._collected_news_envelope("BTC", "비트코인", "q", fetched)
    assert {source["name"] for source in raised.value.sources} == {"google_news_rss", "coindesk_rss"}
    rows = _sources("position_news")
    assert (rows["google"].failures, rows["coindesk"].failures) == (1, 1)


def test_browser_enrichment_folds_pages_into_one_browser_source(monkeypatch, no_snapshots):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    pages = [{"name": "decrypt_index", "publisher": "Decrypt", "kind": "index", "scope": "shared", "url": "https://decrypt.test/"},
             {"name": "decrypt_search", "publisher": "Decrypt", "kind": "search", "scope": "BTC", "url": "https://decrypt.test/search"},
             {"name": "decrypt_tag", "publisher": "Decrypt", "kind": "tag", "scope": "BTC", "url": "https://decrypt.test/tag"}]
    results = {
        news._browser_page_key(pages[0]): {"status": "ready", "items": [_article("Bitcoin price climbs", url="https://decrypt.test/a")]},
        news._browser_page_key(pages[1]): {"status": "error", "items": [], "error": "TimeoutError", "phase": "navigate",
                                           "message": "Browser batch deadline (90s) exceeded"},
        news._browser_page_key(pages[2]): {"status": "ready", "items": [], "cached": True, "attempted": False},
    }
    monkeypatch.setattr(news, "_browser_news_pages", lambda *_: pages)
    monkeypatch.setattr(news, "_cached_browser_pages", lambda descriptors, **_: results)
    payload = news.enrich_coin_news_for_collector("BTC", {"symbol": "BTC", "coin_name": "비트코인", "items": [], "sources": []})
    assert payload["browser_enrichment"]["source_count"] == 3
    rows = _sources("position_news")
    assert set(rows) == {"browser"}
    ready = next(source for source in payload["sources"] if source["name"] == "decrypt_index")
    assert (rows["browser"].calls, rows["browser"].items, rows["browser"].failures, rows["browser"].targets) == (2, ready["item_count"], 1, 1)
    assert rows["browser"].last_error == "TimeoutError Browser batch deadline (90s) exceeded"


# --- 훅: 기사 보강 회차 ---------------------------------------------------------------
class EnrichmentRepo:
    publish_articles = None

    def __init__(self, batches, settled):
        self.batches, self.settled, self.passes = batches, settled, []

    def has_pending_articles(self, *, now_ms=None):
        return True

    def enrichment_stall(self, *, now_ms=None):
        return {"stalled": False, "probing": False}

    def pending_article_batches(self, *, now_ms=None, limit=50):
        return self.batches

    def claim_article_enrichment(self, asset, *, now_ms=None):
        return True

    def enrichment_snapshot(self, asset, ids):
        return {"row": "before"}

    def settle_enrichment_batch(self, asset, before, *, now_ms=None):
        return self.settled

    def record_enrichment_pass(self, progressed, *, now_ms=None):
        self.passes.append(progressed)


def test_enrichment_pass_records_only_when_it_worked_a_batch(monkeypatch):
    monkeypatch.setattr(news, "_localize_coin_news_items", lambda items, **_: [
        {**item, "original_title": item["title"], "title": "한국어 제목"} for item in items])
    item = {"title": "Bitcoin ETF approved", "source": "x", "url": "https://example.test/e"}
    collector.retry_article_enrichment(repo=EnrichmentRepo({}, {"progressed": 0, "retry": 0, "given_up": 0}), now_ms=NOW_MS)
    assert _runs("article_enrichment") == [], "배치가 없는 회차는 남기지 않는다"
    repo = EnrichmentRepo({"BTC": [item], "ETH": [dict(item, url="https://example.test/f")]}, {"progressed": 1, "retry": 2, "given_up": 1})
    collector.retry_article_enrichment(repo=repo, now_ms=NOW_MS)
    (row,) = _runs("article_enrichment")
    assert (row.status, row.targets, row.items, row.failures) == ("ok", 2, 2, 6)
    assert repo.passes == [True]


# --- 훅: 공개 뉴스 범위 실행 -----------------------------------------------------------
def test_public_news_refresh_records_market_and_ticker_runs(monkeypatch):
    finished = []
    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: "token")
    monkeypatch.setattr(public_news, "renew_work", lambda *_: None)
    monkeypatch.setattr(public_news, "finish_work", lambda scope, token, *, retry_seconds=300: finished.append((scope, retry_seconds)))
    monkeypatch.setattr(public_news, "collect_market", lambda *, claim_token=None: {"items": [{"title": "a"}, {"title": "b"}], "collection_status": "ready"})

    def collect_ticker(scope, **kwargs):
        kwargs["fetcher"](scope)
        return {"asset_symbol": scope, "status": "stored", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", collect_ticker)
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector", lambda symbol, **_: {"items": [{"title": "x"}] * 3})
    runtime = public_news.PublicNewsRuntime()

    assert asyncio.run(runtime.refresh("MARKET")) == 300
    assert asyncio.run(runtime.refresh("BTC")) == 300
    rows = _runs("public_news")
    assert [(row.status, row.targets, row.items, json.loads(row.summary_json)["scope"]) for row in rows] == [("ok", 1, 2, "MARKET"), ("ok", 1, 3, "BTC")]
    sources = _sources("public_news")
    assert (sources["market"].calls, sources["market"].items, sources["ticker"].items) == (1, 2, 3)
    assert sources["market"].last_success_ms == rows[0].finished_ms

    def boom(*, claim_token=None):
        raise RuntimeError("feed down")
    monkeypatch.setattr(public_news, "collect_market", boom)
    assert asyncio.run(runtime.refresh("MARKET")) == 30
    last = _runs("public_news")[-1]
    assert (last.status, last.error, last.failures) == ("error", "RuntimeError: feed down", 0)
    market = _sources("public_news")["market"]
    assert (market.calls, market.failures, market.last_error) == (2, 1, "RuntimeError: feed down")
    assert finished == [("MARKET", 300), ("BTC", 300), ("MARKET", 30)], "리스 반납은 기록과 무관하게 그대로"

    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: None)
    assert asyncio.run(runtime.refresh("MARKET")) == 30
    assert len(_runs("public_news")) == 3, "리스를 못 잡은 회차는 실행이 아니다"


def test_public_news_ticker_skips_are_not_calls_and_warming_does_not_count_position_news_sources(monkeypatch):
    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: "token")
    monkeypatch.setattr(public_news, "renew_work", lambda *_: None)
    monkeypatch.setattr(public_news, "finish_work", lambda *_, **__: None)
    runtime = public_news.PublicNewsRuntime()

    # 수집기가 종목 리스를 못 잡으면 fetch 없이 skipped — 실행은 '건너뜀'으로 남고 ticker 소스는 호출로 늘지 않는다.
    monkeypatch.setattr(public_news.collector, "collect_ticker",
                        lambda scope, **_: {"asset_symbol": scope, "status": "skipped", "used_ai_budget": False})
    assert asyncio.run(runtime.refresh("BTC")) == 300
    (row,) = _runs("public_news")
    assert (row.status, row.targets, row.items, json.loads(row.summary_json)["status"]) == ("skipped", 0, 0, "skipped")
    assert _sources("public_news") == {}

    # 다른 실행에 밀려 결과를 버린(superseded) 회차도 같다.
    monkeypatch.setattr(public_news.collector, "collect_ticker",
                        lambda scope, **_: {"asset_symbol": scope, "status": "superseded", "used_ai_budget": False})
    assert asyncio.run(runtime.refresh("ETH")) == 300
    assert [row.status for row in _runs("public_news")] == ["skipped", "skipped"]
    assert _sources("public_news") == {}

    # 워밍이 부른 fetch 안에서 news.py 의 소스 누적 훅이 불려도 position_news 소스로 세지 않는다(이중 집계·upsert 절감).
    envelope_sources = [{"name": "google_news_rss", "status": "ready", "item_count": 3}]

    def fetch_with_hook(symbol, **_):
        news._record_collector_sources(envelope_sources)  # fetch_coin_news_for_collector 의 마지막 envelope 이 부르는 훅
        return {"items": [{"title": "x"}] * 3}

    def collect_ticker(scope, **kwargs):
        kwargs["fetcher"](scope)
        return {"asset_symbol": scope, "status": "stored", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", collect_ticker)
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector", fetch_with_hook)
    assert asyncio.run(runtime.refresh("SOL")) == 300
    assert _sources("position_news") == {}, "워밍이 부른 fetch 는 position_news 소스가 아니다"
    assert (_sources("public_news")["ticker"].calls, _sources("public_news")["ticker"].items) == (1, 3)
    assert [row.status for row in _runs("public_news")] == ["skipped", "skipped", "ok"]

    news._record_collector_sources(envelope_sources)  # 워밍 밖(Prefect flow 등)의 호출은 그대로 기록된다
    assert (_sources("position_news")["google"].calls, _sources("position_news")["google"].items) == (1, 3)


# --- 훅: Prefect flow -------------------------------------------------------------------
class Immediate:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value


class Submitter:
    def __init__(self, function):
        self.function = function

    def submit(self, *args):
        return Immediate(self.function(*args))


def test_position_news_flow_records_a_run_and_a_skipped_late_run(monkeypatch):
    pytest.importorskip("prefect")
    from app.workflows import position_news as workflow

    monkeypatch.setattr(workflow.repository, "claim_collection", lambda *_: "token")
    monkeypatch.setattr(workflow.repository, "finish_collection", lambda *_, **__: True)
    monkeypatch.setattr(workflow.repository, "renew_collection", lambda *_: True)
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: {"active_symbols": ["BTC", "ETH", "SOL"], "due_symbols": ["BTC", "ETH"]})
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", Submitter(lambda symbol: {"symbol": symbol, "items": [{"title": f"{symbol} news"}]}))
    monkeypatch.setattr(workflow, "process_ticker_news_task", Submitter(
        lambda symbol, _payload, allow_ai: {"asset_symbol": symbol, "status": "stored" if symbol == "BTC" else "error",
                                            "error": "" if symbol == "BTC" else "분석 실패", "used_ai_budget": False}))
    monkeypatch.setattr(workflow, "record_fetch_error_task", Submitter(lambda *_: None))
    monkeypatch.setattr(workflow, "prune_snapshots_task", Submitter(lambda _days: 0))

    summary = workflow.collect_position_news_flow.fn()
    assert summary["stored"] == 1 and summary["error"] == 1
    (row,) = _runs("position_news")
    assert (row.status, row.targets, row.items, row.failures, row.error) == ("ok", 2, 1, 1, "")
    stored = json.loads(row.summary_json)
    assert stored["items_count"] == 2 and "items" not in stored and stored["due_ticker_count"] == 2

    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 10_000.0)
    assert workflow.collect_position_news_flow.fn()["run_status"] == "skipped_late"
    assert [row.status for row in _runs("position_news")] == ["ok", "skipped"]


def test_whale_flow_records_run_and_market_sources_even_when_it_raises(monkeypatch):
    pytest.importorskip("prefect")
    from app.workflows import whale_activity as workflow

    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0)
    monkeypatch.setattr(workflow.repository, "prune_inactive_states", lambda: 0)
    monkeypatch.setattr(workflow, "discover_pairs_task", lambda: [
        {"symbol": "BTCUSDT", "market": "spot"}, {"symbol": "BTCUSDT", "market": "futures"}])
    monkeypatch.setattr(workflow, "collect_pair_task", Submitter(lambda symbol, market: (
        {"symbol": symbol, "market": market, "status": "ready", "large_trade_count": 3} if market == "spot"
        else {"symbol": symbol, "market": market, "status": "error", "error_code": "rate_limited", "http_status": 429})))
    with pytest.raises(workflow.WhaleCollectionUnavailable):
        workflow.collect_whale_activity_flow.fn()
    (row,) = _runs("whale_activity")
    assert (row.status, row.targets, row.items, row.failures) == ("error", 2, 3, 1)
    assert row.error.startswith("WhaleCollectionUnavailable: 공개 체결 수집 1건 실패")
    sources = _sources("whale_activity")
    assert (sources["spot"].calls, sources["spot"].items, sources["futures"].failures, sources["futures"].last_error) == (1, 3, 1, "rate_limited 429 (BTCUSDT)")


def test_onchain_flow_records_run_and_provider_source(monkeypatch):
    pytest.importorskip("prefect")
    from app.workflows import onchain_holders as workflow

    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0)
    monkeypatch.setattr(workflow, "discover_coins_task", lambda: ["PEPE", "WETH"])
    monkeypatch.setattr(workflow, "collect_coin_task", lambda coin: {"coin": coin, "source": "Blockscout", "status": "ready", "fetched_count": 40, "tracked_count": 12})
    result = workflow.collect_onchain_holders_flow.fn()
    assert result["checked_coin_count"] == 1
    (row,) = _runs("onchain_holders")
    assert (row.status, row.targets, row.items, row.failures) == ("ok", 2, 40, 0)
    assert _sources("onchain_holders")["Blockscout"].items == 40
