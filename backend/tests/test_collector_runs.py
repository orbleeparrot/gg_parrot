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
from app.agent_features.whale_activity import onchain_collector
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


def test_bump_source_day_follows_the_caller_not_the_clock():
    """자정을 넘긴 회차는 실행 시작일 행에 쌓인다 — 훅이 run.day_kst 를 넘긴다(A12)."""
    after_midnight = int(datetime(2026, 9, 18, 0, 0, 5, tzinfo=_KST).timestamp() * 1000)
    assert collector_runs.bump_source("public_news", "ticker", calls=1, now_ms=after_midnight, day_kst=TODAY)
    collector_runs.bump_sources("onchain_holders", {"blockscout": {"calls": 1}}, now_ms=after_midnight, day_kst=TODAY)
    collector_runs.bump_source_results("whale_activity", [{"market": "spot", "status": "empty"}], source_key_name="market",
                                       items_key="large_trade_count", subject_key="symbol", now_ms=after_midnight, day_kst=TODAY)
    collector_runs.bump_news_sources([{"name": "google_news_rss", "status": "ready"}], now_ms=after_midnight, day_kst=TODAY)
    with get_session() as db:
        rows = db.exec(select(CollectorSourceDaily)).all()
    assert {(row.engine, row.source, row.day_kst) for row in rows} == {
        ("public_news", "ticker", TODAY), ("onchain_holders", "blockscout", TODAY),
        ("whale_activity", "spot", TODAY), ("position_news", "google", TODAY)}
    run = collector_runs.RunRecorder("public_news", now_ms=NOW_MS)
    assert run.day_kst == TODAY


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
    assert engines[3]["mode"] == "웹 · MARKET + 종목별 · 범위마다 1회" and engines[5]["mode"] == "Prefect · 수동"
    assert engines[4]["mode"] == "웹 · 5초 스캔(일감 있을 때만 기록)"
    by = {row["engine"]: row for row in engines}
    assert (by["position_news"]["status"], by["position_news"]["status_label"]) == ("ok", "정상")
    assert (by["position_news"]["runs_today"], by["position_news"]["skipped_today"], by["position_news"]["targets_today"],
            by["position_news"]["items_today"], by["position_news"]["failures_today"]) == (2, 0, 10, 10, 4)
    assert by["position_news"]["last_error"] == "Google RSS 503" and by["position_news"]["last_run_ms"] == NOW_MS - 30_000
    assert (by["whale_activity"]["status"], by["whale_activity"]["status_label"]) == ("delayed", "지연")
    assert (by["onchain_holders"]["status"], by["onchain_holders"]["last_error"]) == ("error", "blockscout 429")
    assert by["public_news"]["status"] == "ok" and by["public_news"]["served_from_cache_today"] == 0
    assert "served_from_cache_today" not in by["position_news"]
    assert (by["article_enrichment"]["status"], by["article_enrichment"]["status_label"], by["article_enrichment"]["last_run_ms"]) == ("idle", "대기", 0)
    assert by["coindesk_probe"]["status"] == "ok" and by["coindesk_probe"]["runs_today"] == 0

    hourly = report["hourly"]
    assert [row["hour"] for row in hourly] == list(range(24))
    assert hourly[9] == {"hour": 9, "items": 10, "failures": 1} and hourly[8] == {"hour": 8, "items": 0, "failures": 3}
    assert sum(row["items"] for row in hourly) == 10

    sources = report["sources"]
    # 소스별 표에는 모든 엔진의 소스가 들어간다(고래 'spot' 포함) — 순서는 SOURCE_LABELS, 모르는 키는 뒤로.
    assert [(row["source"], row["label"]) for row in sources] == [
        ("google", "Google News RSS"), ("browser", "브라우저 보강 (Playwright)"), ("spot", "Binance 현물"), ("zeta_feed", "zeta_feed")]
    assert [(row["engine"], row["engine_label"]) for row in sources] == [
        ("position_news", "종목 뉴스 수집"), ("position_news", "종목 뉴스 수집"), ("whale_activity", "고래 거래 수집"),
        ("position_news", "종목 뉴스 수집")]
    browser = sources[1]
    assert (browser["calls"], browser["items"], browser["failures"], browser["failure_pct"], browser["last_error"]) == (8, 3, 2, 25.0, "TimeoutError")
    assert (sources[0]["calls"], sources[0]["failure_pct"], sources[0]["last_success_ms"]) == (4, 0.0, NOW_MS), "어제 행은 빠진다"


def test_hourly_chart_sums_position_and_public_news_runs():
    """시간대별 수집 = 종목 뉴스 + 공개 뉴스(운영에서는 워밍이 대부분을 수집한다). 다른 엔진은 섞지 않는다(A5)."""
    with get_session() as db:
        db.add_all([_run_row("position_news", age_s=30, items=3, failures=1),
                    _run_row("public_news", age_s=60, items=7, failures=1),
                    _run_row("public_news", age_s=3600, items=2),        # 09:00 KST
                    _run_row("onchain_holders", age_s=10, items=100)])
        db.commit()
        hourly = collector_runs.engines_report(db, now_ms=NOW_MS)["hourly"]
    assert hourly[9] == {"hour": 9, "items": 10, "failures": 2} and hourly[8] == {"hour": 8, "items": 2, "failures": 0}
    assert sum(row["items"] for row in hourly) == 12


def test_engine_status_source_failed_skipped_and_todays_error_only():
    """A9·A10: 소스 실패 상태는 저장값 그대로 두고 화면은 '오류(소스 실패)'; 건너뜀은 runs_today 밖; last_error 는 오늘 것만."""
    with get_session() as db:
        db.add_all([_run_row("position_news", age_s=10, status="degraded", targets=3, items=2, failures=1),
                    _run_row("position_news", age_s=100, status="skipped"),
                    _run_row("position_news", age_s=200, status="skipped_late"),
                    _run_row("position_news", age_s=300, status="ok", targets=3, items=5),
                    _run_row("onchain_holders", age_s=5, status="source_unavailable"),
                    _run_row("whale_activity", age_s=5, status="skipped"),
                    _run_row("whale_activity", age_s=86_400 + 10, status="error", error="어제 오류")])  # 어제
        db.commit()
        by = {row["engine"]: row for row in collector_runs.engines_report(db, now_ms=NOW_MS)["engines"]}
    assert (by["position_news"]["status"], by["position_news"]["status_label"]) == ("error", "소스 실패")
    assert (by["position_news"]["runs_today"], by["position_news"]["skipped_today"], by["position_news"]["targets_today"],
            by["position_news"]["items_today"], by["position_news"]["failures_today"]) == (2, 2, 6, 7, 1)
    assert (by["onchain_holders"]["status"], by["onchain_holders"]["status_label"]) == ("error", "소스 실패")
    assert (by["whale_activity"]["runs_today"], by["whale_activity"]["skipped_today"]) == (0, 1)
    assert by["whale_activity"]["last_error"] == "", "어제 오류는 오늘 표에 보이지 않는다"
    assert collector_runs.STATUS_LABELS["skipped"] == "건너뜀"
    with get_session() as db:
        assert {row.status for row in db.exec(select(CollectorRun)).all()} >= {"degraded", "source_unavailable", "skipped_late"}, "저장값은 그대로"


def _enrichment_status(db):
    row = collector_runs.engines_report(db, now_ms=NOW_MS)["engines"][4]
    return row["status"], row["status_label"]


def test_enrichment_stall_that_persists_becomes_an_error(monkeypatch):
    """공급자가 몇 시간 죽어 있어도 '정체 쉼' 만 보이던 문제 — **정체가 시작된 지**(마지막 진전 뒤 첫 무진전 회차)
    정체 쉼 × 10(10분) 을 넘겨야 '정체 지속' 이다(A9). 마지막 진전 시각을 기준으로 재면 안 된다(아래 마지막 경우)."""
    monkeypatch.setenv("POSITION_NEWS_ENRICHMENT_STALL_SECONDS", "60")
    with get_session() as db:
        # 공급자가 5분째 죽어 있다: 진전 15분 전, 무진전 회차는 5분 전부터 → 아직 '정체 쉼'.
        db.add_all([_run_row("article_enrichment", age_s=900, items=3),
                    _run_row("article_enrichment", age_s=300, items=0, targets=2),   # 정체 시작
                    _run_row("article_enrichment", age_s=150, items=0, targets=2),
                    _run_row("article_enrichment", age_s=20, items=0, targets=2)])
        db.commit()
        articles.record_enrichment_pass(False, now_ms=NOW_MS - 18_000, db=db)  # 정체 리스 (42초 남음)
        assert _enrichment_status(db) == ("stalled", "정체 쉼 · 42초 뒤 탐침")
        # 11분째 죽어 있다: 첫 무진전 회차가 11분 전 → '정체 지속'.
        db.add(_run_row("article_enrichment", age_s=660, items=0, targets=2))
        db.commit()
        assert _enrichment_status(db) == ("error", "정체 지속")
        # 정체가 풀리면(진전 있는 회차) 오류가 아니다.
        articles.record_enrichment_pass(True, now_ms=NOW_MS, db=db)
        assert _enrichment_status(db)[0] == "ok"

        # 진전한 실행이 한 번도 없으면 첫 실행이 곧 정체 시작이다.
        db.exec(delete(CollectorRun))
        db.add(_run_row("article_enrichment", age_s=700, items=0, targets=1))
        db.commit()
        articles.record_enrichment_pass(False, now_ms=NOW_MS - 18_000, db=db)
        assert _enrichment_status(db) == ("error", "정체 지속")

        # 마지막 진전이 몇 시간 전이어도 그 사이 조용했다가(대기 기사 없음) 방금 막힌 첫 회차면 정체는 이제 시작이다.
        db.exec(delete(CollectorRun))
        db.add_all([_run_row("article_enrichment", age_s=3 * 3600, items=5),
                    _run_row("article_enrichment", age_s=20, items=0, targets=2)])
        db.commit()
        assert _enrichment_status(db) == ("stalled", "정체 쉼 · 42초 뒤 탐침"), "마지막 진전 시각이 아니라 정체 시작이 기준"


def test_expired_enrichment_stall_lease_with_empty_queue_is_idle_not_stalled(monkeypatch):
    """리스는 만료됐고 보강할 기사도 없다 — 마지막 회차가 무진전이었다는 흔적만 남은 상태는 '정체 지속' 이 아니다."""
    monkeypatch.setenv("POSITION_NEWS_ENRICHMENT_STALL_SECONDS", "60")
    with get_session() as db:
        db.add(_run_row("article_enrichment", age_s=700, items=0, targets=1))
        db.commit()
        articles.record_enrichment_pass(False, now_ms=NOW_MS - 120_000, db=db)  # 리스는 1분 전에 만료
        monkeypatch.setattr(collector_runs, "_enrichment_has_due_work", lambda _db, _ms: False)
        assert collector_runs._enrichment_stall_persisted(db, NOW_MS) is False
        assert _enrichment_status(db) == ("ok", "정상"), "큐가 비어 안 도는 것은 정체도 지연도 아니다"
        # 같은 상태에서 일감이 있으면(루프가 탐침을 안 보내고 있다) 정체 지속이다.
        monkeypatch.setattr(collector_runs, "_enrichment_has_due_work", lambda _db, _ms: True)
        assert _enrichment_status(db) == ("error", "정체 지속")
        # 실행 행이 하나도 없으면 리스가 걸린 시각(next_run - 쉼)이 정체 시작이다.
        db.exec(delete(CollectorRun))
        db.commit()
        articles.record_enrichment_pass(False, now_ms=NOW_MS - 18_000, db=db)
        assert collector_runs._enrichment_stall_persisted(db, NOW_MS) is False
        articles.record_enrichment_pass(False, now_ms=NOW_MS - 700_000, db=db)
        assert collector_runs._enrichment_stall_persisted(db, NOW_MS) is True


def test_sources_table_includes_onchain_and_whale_engines_with_engine_labels():
    """온체인·고래 소스 행이 화면에 없어 blockscout 실패 18건이 보이지 않았다(A7). 호출 0 이면 실패율은 None."""
    collector_runs.bump_source("onchain_holders", "blockscout", calls=9, items=48, failures=1, error="429 (PEPE)", now_ms=NOW_MS)
    collector_runs.bump_source("onchain_holders", "xrpscan", calls=1, items=50, success_ms=NOW_MS, now_ms=NOW_MS)
    collector_runs.bump_source("whale_activity", "futures", calls=0, items=0, now_ms=NOW_MS)
    collector_runs.bump_source("position_news", "google", calls=1, items=1, now_ms=NOW_MS)
    collector_runs.bump_source("public_news", "google", calls=2, items=3, now_ms=NOW_MS)
    with get_session() as db:
        rows = {row["source"]: row for row in collector_runs.engines_report(db, now_ms=NOW_MS)["sources"]}
    assert (rows["blockscout"]["label"], rows["blockscout"]["engine"], rows["blockscout"]["engine_label"]) == (
        "Blockscout", "onchain_holders", "온체인 보유 수집")
    assert (rows["blockscout"]["calls"], rows["blockscout"]["failures"], rows["blockscout"]["failure_pct"]) == (9, 1, 11.1)
    assert (rows["xrpscan"]["label"], rows["futures"]["label"], rows["futures"]["engine_label"]) == ("XRPScan", "Binance 선물", "고래 거래 수집")
    assert rows["futures"]["failure_pct"] is None, "호출이 없으면 실패율도 없다"
    assert (rows["google"]["engine"], rows["google"]["engine_label"], rows["google"]["calls"]) == (
        "position_news+public_news", "종목 뉴스 수집 + 공개 뉴스 (코인동향)", 3)


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
    # 실패 = 포기한 행만. 다음 회차로 미룬 행(retry)은 실패가 아니라 요약의 deferred 다 — 회차마다 다시 세어 50배 부풀었다(A1).
    assert (row.status, row.targets, row.items, row.failures) == ("ok", 2, 2, 2)
    assert json.loads(row.summary_json) == {"progressed": 2, "deferred": 4, "given_up": 2}
    assert repo.passes == [True]


# --- 훅: 공개 뉴스 범위 실행 -----------------------------------------------------------
@pytest.fixture
def public_scope():
    """공개 뉴스 회차 테스트가 실제 upsert 로 남긴 기사 행을 치운다."""
    scope = "CRPUB"
    def clear():
        with get_session() as db:
            db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == scope))
            db.exec(delete(articles.NewsArticleFeed).where(articles.NewsArticleFeed.asset_symbol == scope))
            db.commit()
    clear()
    yield scope
    clear()


def _fetched(items, *, cached=False):
    """수집기 fetch 응답 모양 — sources 가 있어야 '실제 호출' 로 본다(없으면 캐시 응답)."""
    return {"items": items, "sources": [{"name": "google_news_rss", "status": "ready", "item_count": len(items), "cached": cached}]}


def test_public_news_refresh_records_market_and_ticker_runs(monkeypatch, public_scope):
    finished = []
    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: "token")
    monkeypatch.setattr(public_news, "renew_work", lambda *_: None)
    monkeypatch.setattr(public_news, "finish_work", lambda scope, token, *, retry_seconds=300: finished.append((scope, retry_seconds)))
    monkeypatch.setattr(public_news, "collect_market", lambda *, claim_token=None: {"items": [{"title": "a"}, {"title": "b"}], "collection_status": "ready"})

    stories = [{"title": f"공개 뉴스 회차 기사 {n}", "source": "x", "url": f"https://example.test/pub{n}"} for n in range(3)]

    def collect_ticker(scope, **kwargs):
        kwargs["fetcher"](scope)
        # 수집기가 저장소로 두 번 저장해도(진행 콜백·최종) '수집' 은 새로 insert 된 행만 센다(A6).
        kwargs["repo"].publish_articles(scope, {"items": stories[:2]})
        kwargs["repo"].publish_articles(scope, {"items": stories})
        return {"asset_symbol": scope, "status": "stored", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", collect_ticker)
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector", lambda symbol, **_: _fetched(stories))
    runtime = public_news.PublicNewsRuntime()

    assert asyncio.run(runtime.refresh("MARKET")) == 300
    assert asyncio.run(runtime.refresh(public_scope)) == 300
    rows = _runs("public_news")
    assert [(row.status, row.targets, row.items, json.loads(row.summary_json)["scope"]) for row in rows] == [("ok", 1, 2, "MARKET"), ("ok", 1, 3, public_scope)]
    sources = _sources("public_news")
    assert (sources["market"].calls, sources["market"].items, sources["ticker"].items) == (1, 2, 3)
    assert sources["market"].last_success_ms == rows[0].finished_ms
    # 같은 기사를 다시 수집한 회차는 새 행이 없으니 수집 0 이다(캐시로 내보낸 목록 크기를 세어 10.6배 부풀던 값).
    assert asyncio.run(runtime.refresh(public_scope)) == 300
    assert (_runs("public_news")[-1].status, _runs("public_news")[-1].items) == ("ok", 0)
    assert (_sources("public_news")["ticker"].calls, _sources("public_news")["ticker"].items) == (2, 3)

    def boom(*, claim_token=None):
        raise RuntimeError("feed down")
    monkeypatch.setattr(public_news, "collect_market", boom)
    assert asyncio.run(runtime.refresh("MARKET")) == 30
    last = _runs("public_news")[-1]
    assert (last.status, last.error, last.failures) == ("error", "RuntimeError: feed down", 0)
    market = _sources("public_news")["market"]
    assert (market.calls, market.failures, market.last_error) == (2, 1, "RuntimeError: feed down")
    assert finished == [("MARKET", 300), (public_scope, 300), (public_scope, 300), ("MARKET", 30)], "리스 반납은 기록과 무관하게 그대로"

    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: None)
    assert asyncio.run(runtime.refresh("MARKET")) == 30
    assert len(_runs("public_news")) == 4, "리스를 못 잡은 회차는 실행이 아니다"


def test_public_news_rounds_served_from_cache_make_no_source_row(monkeypatch):
    """캐시·스냅샷으로 응답한 회차는 HTTP 요청이 없으니 소스 행(호출)을 만들지 않고 캐시 응답으로만 센다(A13)."""
    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: "token")
    monkeypatch.setattr(public_news, "renew_work", lambda *_: None)
    monkeypatch.setattr(public_news, "finish_work", lambda *_, **__: None)

    def collect_ticker(scope, **kwargs):
        kwargs["fetcher"](scope)
        return {"asset_symbol": scope, "status": "reused", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", collect_ticker)
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector",
                        lambda symbol, **_: _fetched([{"title": "x"}] * 8, cached=True))
    monkeypatch.setattr(public_news, "collect_market", lambda *, claim_token=None: {
        "items": [{"title": "a"}] * 8, "item_count": 0, "served_from_cache": True})
    runtime = public_news.PublicNewsRuntime()
    assert asyncio.run(runtime.refresh("BTC")) == 300
    assert asyncio.run(runtime.refresh("MARKET")) == 300
    rows = _runs("public_news")
    assert [(row.status, row.targets, row.items, json.loads(row.summary_json)["served_from_cache"]) for row in rows] == [
        ("cached", 1, 0, True), ("cached", 1, 0, True)]
    assert _sources("public_news") == {}, "호출이 없었으니 ticker/market 행도 없다"
    with get_session() as db:
        entry = {row["engine"]: row for row in collector_runs.engines_report(db)["engines"]}["public_news"]
    assert (entry["runs_today"], entry["skipped_today"], entry["served_from_cache_today"], entry["status"]) == (0, 0, 2, "ok")


def test_public_news_fetch_that_raises_is_a_failed_call_not_a_cache_hit(monkeypatch, public_scope):
    """스냅샷 없는 종목의 fetch 가 NewsFetchError 로 끝나면 '캐시 응답' 이 아니다 — 호출은 있었고 실패했다(F3).
    수집기는 enricher 가 있으면 fetch 예외를 삼키고 회차를 마치므로, 예전 기본값(attempted=False)이 그대로 남아 소스가
    죽은 회차가 관리자 표에 '캐시 응답' 으로 보였다."""
    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: "token")
    monkeypatch.setattr(public_news, "renew_work", lambda *_: None)
    monkeypatch.setattr(public_news, "finish_work", lambda *_, **__: None)

    def failing_fetch(symbol, **_):
        raise news.NewsFetchError("Google unavailable", sources=[{"name": "google_news_rss", "status": "error"}])
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector", failing_fetch)

    def swallowing_collect_ticker(scope, **kwargs):
        try:  # 실제 수집기처럼: enricher 가 있으면 fetch 예외를 삼키고 빈 회차로 마친다
            kwargs["fetcher"](scope)
        except news.NewsFetchError:
            pass
        return {"asset_symbol": scope, "status": "empty", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", swallowing_collect_ticker)
    runtime = public_news.PublicNewsRuntime()
    assert asyncio.run(runtime.refresh(public_scope)) == 300
    (row,) = _runs("public_news")
    assert (row.status, row.targets, row.items, row.failures) == ("ok", 1, 0, 1)
    assert row.error.startswith("NewsFetchError") and json.loads(row.summary_json)["served_from_cache"] is False
    ticker = _sources("public_news")["ticker"]
    assert (ticker.calls, ticker.failures, ticker.last_success_ms, ticker.last_error) == (1, 1, 0, row.error)
    with get_session() as db:
        entry = {r["engine"]: r for r in collector_runs.engines_report(db)["engines"]}["public_news"]
    assert (entry["runs_today"], entry["served_from_cache_today"], entry["failures_today"]) == (1, 0, 1)

    # 수집기가 예외를 그대로 올리면 실행은 오류다 — 이때도 캐시 응답이 아니다.
    monkeypatch.setattr(public_news.collector, "collect_ticker", lambda scope, **kwargs: kwargs["fetcher"](scope))
    assert asyncio.run(runtime.refresh(public_scope)) == 30
    last = _runs("public_news")[-1]
    assert (last.status, last.error) == ("error", "NewsFetchError: Google unavailable")
    assert (_sources("public_news")["ticker"].calls, _sources("public_news")["ticker"].failures) == (2, 2)


def test_coin_news_served_from_rss_cache_marks_sources_unattempted(monkeypatch, no_snapshots):
    """_coin_cache 적중으로 응답한 봉투의 소스는 이번 호출에 HTTP 요청이 없었으니 cached·attempted=False 다(F6).
    그대로 두면 워밍 회차가 '실제 호출' 로 세어 ticker 소스 행이 생긴다. 캐시 항목 자체는 바꾸지 않는다."""
    raw = {"symbol": "BTC", "items": [], "data_source": "rss_cache",
           "sources": [{"name": "google_news_rss", "status": "ready", "item_count": 0}]}
    monkeypatch.setattr(news, "_coin_envelope_cache", {}, raising=False)
    monkeypatch.setattr(news, "_coin_cache", {"coin:BTC": (raw, time.time() + 300)})
    monkeypatch.setattr(news, "_localize_news_payload", lambda payload, **_: dict(payload))
    monkeypatch.setattr(news, "_fetch_public_news_payload", lambda *_: pytest.fail("캐시가 살아 있으면 받아오지 않는다"))

    env = news.get_coin_news("BTCUSDT")
    assert env["sources"] == [{"name": "google_news_rss", "status": "ready", "item_count": 0, "cached": True, "attempted": False}]
    assert news._coin_cache["coin:BTC"][0]["sources"] == [{"name": "google_news_rss", "status": "ready", "item_count": 0}], "캐시 항목은 그대로"
    assert public_news._any_source_attempted(env["sources"]) is False

    # 그 봉투를 받은 워밍 회차는 캐시 응답으로 남고 ticker 소스 행을 만들지 않는다.
    monkeypatch.setattr(public_news, "claim_work", lambda scope, **_: "token")
    monkeypatch.setattr(public_news, "renew_work", lambda *_: None)
    monkeypatch.setattr(public_news, "finish_work", lambda *_, **__: None)
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector", lambda symbol, **_: news.get_coin_news(symbol))

    def collect_ticker(scope, **kwargs):
        kwargs["fetcher"](scope)
        return {"asset_symbol": scope, "status": "reused", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", collect_ticker)
    assert asyncio.run(public_news.PublicNewsRuntime().refresh("BTC")) == 300
    (row,) = _runs("public_news")
    assert (row.status, row.failures, json.loads(row.summary_json)["served_from_cache"]) == ("cached", 0, True)
    assert _sources("public_news") == {}, "호출이 없었으니 ticker 행도 없다"


def test_any_source_attempted_treats_cached_and_unattempted_as_no_call():
    assert public_news._any_source_attempted(None) is False
    assert public_news._any_source_attempted([{"name": "google_news_rss", "status": "ready", "cached": True},
                                              {"name": "binance_square", "status": "disabled"}]) is False
    assert public_news._any_source_attempted([{"name": "google_news_rss", "status": "error"}]) is True


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
        return {"items": [{"title": "x"}] * 3, "sources": envelope_sources}

    def collect_ticker(scope, **kwargs):
        kwargs["fetcher"](scope)
        return {"asset_symbol": scope, "status": "stored", "used_ai_budget": False}
    monkeypatch.setattr(public_news.collector, "collect_ticker", collect_ticker)
    monkeypatch.setattr(public_news.news, "fetch_coin_news_for_collector", fetch_with_hook)
    assert asyncio.run(runtime.refresh("SOL")) == 300
    assert _sources("position_news") == {}, "워밍이 부른 fetch 는 position_news 소스가 아니다"
    assert (_sources("public_news")["ticker"].calls, _sources("public_news")["ticker"].items) == (1, 0), "저장한 새 행이 없으면 수집 0"
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
    monkeypatch.setattr(workflow, "collect_coin_task", lambda coin: {"coin": coin, "source": "blockscout", "status": "ready", "fetched_count": 40, "tracked_count": 12})
    result = workflow.collect_onchain_holders_flow.fn()
    assert result["checked_coin_count"] == 1 and result["fetched_count"] == 40
    (row,) = _runs("onchain_holders")
    # '수집' 은 저장한 보유 행(tracked)이다 — 원본 파싱 행(fetched)은 요약에만(A3).
    assert (row.status, row.targets, row.items, row.failures) == ("ok", 2, 12, 0)
    assert json.loads(row.summary_json)["fetched_count"] == 40
    assert _sources("onchain_holders")["blockscout"].items == 12


def test_onchain_failures_carry_the_source_name(monkeypatch):
    """실패 결과에 소스가 없으면 관리자 표가 'unknown' 소스에 실패를 쌓아 blockscout 실패율이 0% 로 보였다(A2)."""
    from app import whales

    monkeypatch.setattr(onchain_collector.repository, "claim_collection", lambda coin, **_: "token")
    monkeypatch.setattr(onchain_collector.repository, "record_failure", lambda *_, **__: True)
    monkeypatch.setattr(onchain_collector.repository, "store_result", lambda *_, **__: (_ for _ in ()).throw(ValueError("bad")))

    def rate_limited(_coin):
        raise whales.OnchainSourceError("rate_limited", http_status=429, retry_after_seconds=60)
    results = [
        onchain_collector.collect_coin("PEPE", fetcher=rate_limited),
        onchain_collector.collect_coin("XRP", fetcher=lambda _coin: (_ for _ in ()).throw(RuntimeError("boom"))),
        onchain_collector.collect_coin("WETH", fetcher=lambda _coin: {"coin": "WETH"}),
    ]
    assert [(row["status"], row["source"], row["error_code"]) for row in results] == [
        ("error", "blockscout", "rate_limited"), ("error", "xrpscan", "invalid_response"), ("error", "blockscout", "invalid_response")]
    collector_runs.bump_source_results("onchain_holders", results, source_key_name="source", items_key="tracked_count",
                                       subject_key="coin", now_ms=NOW_MS)
    sources = _sources("onchain_holders")
    assert set(sources) == {"blockscout", "xrpscan"} and "unknown" not in sources
    assert (sources["blockscout"].failures, sources["blockscout"].last_error) == (2, "invalid_response (WETH)")


def test_sources_table_folds_warming_and_worker_by_source_key():
    """같은 크롤러를 공개 뉴스 워밍과 종목 뉴스 워커가 번갈아 불러도 화면에는 소스 하나로 합쳐 보인다.

    운영에서는 워밍이 리스를 먼저 쥐는 일이 잦다 — 엔진으로 갈라 두면 그날 표가 통째로 빈다(2026-09-17).
    """
    now = 1_760_000_000_000
    collector_runs.bump_sources(collector_runs.ENGINE_POSITION_NEWS,
                                {"google": {"calls": 2, "items": 10, "failures": 0, "targets": 2, "success_ms": now}},
                                now_ms=now)
    collector_runs.bump_sources(collector_runs.ENGINE_PUBLIC_NEWS,
                                {"google": {"calls": 3, "items": 5, "failures": 1, "targets": 3, "error": "HTTP 429",
                                            "success_ms": 0}},
                                now_ms=now + 1_000)
    # 범위 단위 카운터는 소스 표에 끼면 안 된다(엔진 표의 몫).
    collector_runs.bump_source(collector_runs.ENGINE_PUBLIC_NEWS, "ticker", calls=4, items=60, now_ms=now)

    with get_session() as db:
        report = collector_runs.engines_report(db, now_ms=now + 2_000)
    rows = {row["source"]: row for row in report["sources"]}
    assert "ticker" not in rows and "market" not in rows
    google = rows["google"]
    assert (google["calls"], google["items"], google["failures"], google["targets"]) == (5, 15, 1, 5)
    assert google["failure_pct"] == 20.0 and google["last_error"] == "HTTP 429"
    assert google["last_success_ms"] == now and google["label"] == "Google News RSS"


def test_public_news_warming_records_sources_under_its_own_engine(monkeypatch):
    """워밍 중 온 소스 기록은 버리지 않고 public_news 로 남긴다 — 버리면 소스 표가 종일 빈다."""
    from app import public_news

    seen = []
    monkeypatch.setattr(collector_runs, "bump_news_sources",
                        lambda sources, **kw: seen.append((len(sources), kw.get("engine"))))
    plain = public_news._attribute_source_records(lambda sources: seen.append((len(sources), "passthrough")))

    plain([{"name": "google_news_rss", "status": "ready", "item_count": 3}])
    flag = public_news._warming.set(True)
    try:
        plain([{"name": "google_news_rss", "status": "ready", "item_count": 3}])
    finally:
        public_news._warming.reset(flag)
    assert seen == [(1, "passthrough"), (1, collector_runs.ENGINE_PUBLIC_NEWS)]


def test_market_scope_records_its_sources_once_and_keeps_warming_attribution(monkeypatch, no_snapshots):
    """MARKET 범위는 소스별 표에 한 줄도 없었다(훅이 종목 경로에만) — 최종 소스 목록을 한 번 넘긴다(A8)."""
    monkeypatch.setattr(news, "_fetch_news", lambda query, **_: [_article("Bitcoin regulation update", url="https://news.test/m1")])
    payload = news._fetch_public_news_payload()
    rows = _sources("position_news")
    assert list(rows) == ["google"] and (rows["google"].calls, rows["google"].items) == (1, len(payload["items"]))
    assert _sources("public_news") == {}

    # 워밍(공개 뉴스 collect_market) 안에서는 public_news 엔진으로 남는다.
    monkeypatch.setattr(news, "_load_durable_market_summary", lambda _day: "요약")
    monkeypatch.setattr(news, "_localize_news_payload", lambda raw, **_: dict(raw))
    monkeypatch.setattr(public_news.news_images, "attach", lambda items: "ready")
    with get_session() as db:
        db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == "MARKET"))
        db.exec(delete(articles.NewsArticleFeed).where(articles.NewsArticleFeed.asset_symbol == "MARKET"))
        db.commit()
    try:
        result = public_news.collect_market()
        assert (result["item_count"], result["served_from_cache"]) == (1, False), "새로 저장한 기사 1건"
        assert public_news.collect_market()["item_count"] == 0, "같은 기사를 다시 받은 회차는 수집 0"
    finally:
        with get_session() as db:
            db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == "MARKET"))
            db.exec(delete(articles.NewsArticleFeed).where(articles.NewsArticleFeed.asset_symbol == "MARKET"))
            db.commit()
    assert (_sources("public_news")["google"].calls, _sources("position_news")["google"].calls) == (2, 1)

    # 전부 실패한 회차도 raise 앞에서 실패로 남긴다.
    def broken(query, **_):
        raise news.NewsFetchError("down")
    monkeypatch.setattr(news, "_fetch_news", broken)
    monkeypatch.setattr(news, "_fetch_public_news_fallback", lambda *_, **__: {"items": [], "sources": [
        {"name": "coindesk_rss", "status": "error", "item_count": 0}]})
    with pytest.raises(news.NewsFetchError):
        news._fetch_public_news_payload()
    rows = _sources("position_news")
    assert (rows["google"].failures, rows["coindesk"].failures) == (1, 1)


# --- 관리자 news_report: 오늘 실패 KPI · 포기 오늘/누적 ------------------------------------------
def test_admin_news_report_failures_kpi_sums_all_engines_and_splits_given_up(monkeypatch):
    """'오늘 실패' 는 표의 여섯 엔진 실패 합(A5), '포기' 는 오늘/누적 두 줄(A11)."""
    from app import admin

    with get_session() as db:
        db.add_all([_run_row("position_news", age_s=10, failures=1), _run_row("onchain_holders", age_s=10, failures=18),
                    _run_row("article_enrichment", age_s=10, failures=3), _run_row("whale_activity", age_s=86_400 + 10, failures=9)])
        db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == "CRADM"))
        db.commit()
    yesterday = NOW_MS - 86_400_000
    with get_session() as db:
        db.add_all([
            articles.NewsArticle(asset_symbol="CRADM", article_id="a1", revision=1, item_json="{}", first_seen_ms=NOW_MS - 1000,
                                 last_seen_ms=NOW_MS, enrichment_pending=False, enrichment_attempts=5),
            articles.NewsArticle(asset_symbol="CRADM", article_id="a2", revision=2, item_json="{}", first_seen_ms=yesterday,
                                 last_seen_ms=yesterday, enrichment_pending=False, enrichment_attempts=5),
            articles.NewsArticle(asset_symbol="CRADM", article_id="a3", revision=3, item_json="{}", first_seen_ms=NOW_MS - 1000,
                                 last_seen_ms=NOW_MS, enrichment_pending=False, enrichment_attempts=0),
        ])
        db.commit()
    monkeypatch.setattr(admin, "_now", lambda: ("2026-09-17T01:00:00Z", NOW_MS))
    try:
        with get_session() as db:
            report = admin._news_report(db)
    finally:
        with get_session() as db:
            db.exec(delete(articles.NewsArticle).where(articles.NewsArticle.asset_symbol == "CRADM"))
            db.commit()
    assert report["kpis"]["failures_today"] == 22, "어제 고래 실패 9 는 빠진다"
    enrichment = {row["key"]: row for row in report["enrichment"]}
    assert enrichment["given_up_today"]["value"] == "1" and enrichment["given_up_today"]["label"].startswith("오늘 포기")
    assert enrichment["given_up"]["value"] == "2" and enrichment["given_up"]["label"].startswith("누적 포기")
