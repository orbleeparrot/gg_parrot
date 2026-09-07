"""Prefect adapter tests call the underlying flow function with local fakes."""
from __future__ import annotations

import pytest

pytest.importorskip("sqlmodel")
pytest.importorskip("prefect")

from app.workflows import position_news as workflow


@pytest.fixture(autouse=True)
def collection_leases(monkeypatch):
    monkeypatch.setattr(workflow.repository, "claim_collection", lambda *_: "token")
    monkeypatch.setattr(workflow.repository, "finish_collection", lambda *_: True)
    monkeypatch.setattr(workflow.repository, "renew_collection", lambda *_: True)


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


class ForbiddenSubmitter:
    def submit(self, *_args, **_kwargs):
        raise AssertionError("inactive collection must not submit source or AI tasks")


class RecordingPruner:
    def __init__(self):
        self.calls = []

    def submit(self, retention_days):
        self.calls.append(retention_days)
        return Immediate(0)


def _payload(symbol):
    return {
        "symbol": symbol,
        "coin_name": symbol,
        "items": [{"title": f"{symbol} headline", "source": "test"}],
    }


def test_flow_uses_base_tickers_and_separates_model_stage(
    monkeypatch,
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "1")
    monkeypatch.setenv("POSITION_NEWS_MAX_CYCLE_SECONDS", "240")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(
        workflow,
        "discover_tickers_task",
        lambda: ["BTC", "ETH"],
    )
    monkeypatch.setattr(
        workflow,
        "fetch_ticker_news_task",
        Submitter(lambda symbol: _payload(symbol)),
    )
    allowed = []

    def process(symbol, _payload_value, allow_ai):
        allowed.append((symbol, allow_ai))
        return {
            "asset_symbol": symbol,
            "status": "stored",
            "used_ai_budget": allow_ai,
        }

    monkeypatch.setattr(
        workflow,
        "process_ticker_news_task",
        Submitter(process),
    )
    monkeypatch.setattr(
        workflow,
        "record_fetch_error_task",
        Submitter(lambda *_args: None),
    )
    monkeypatch.setattr(
        workflow,
        "prune_snapshots_task",
        Submitter(lambda _days: 0),
    )

    summary = workflow.collect_position_news_flow.fn()

    assert allowed == [("BTC", True), ("ETH", False)]
    assert summary["stored"] == 2
    assert summary["ai_budget_used"] == 1


def test_flow_with_no_active_tickers_skips_source_and_ai_but_prunes(monkeypatch):
    prune = RecordingPruner()
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: [])
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", ForbiddenSubmitter())
    monkeypatch.setattr(workflow, "process_ticker_news_task", ForbiddenSubmitter())
    monkeypatch.setattr(workflow, "record_fetch_error_task", ForbiddenSubmitter())
    monkeypatch.setattr(workflow, "prune_snapshots_task", prune)

    summary = workflow.collect_position_news_flow.fn()

    assert summary["ticker_count"] == 0
    assert summary["ai_budget_used"] == 0
    assert summary["items"] == []
    assert prune.calls == [30]


def test_prefect_retry_and_timeout_metadata():
    assert workflow.fetch_ticker_news_task.retries == 2
    assert workflow.fetch_ticker_news_task.retry_delay_seconds == [5, 15]
    assert workflow.process_ticker_news_task.retries == 0
    assert workflow.publish_initial_news_task.retries == 0
    assert workflow.enrich_ticker_news_task.retries == 0
    assert workflow.collect_position_news_flow.retries == 0

    tasks = (
        workflow.fetch_ticker_news_task,
        workflow.publish_initial_news_task,
        workflow.enrich_ticker_news_task,
        workflow.process_ticker_news_task,
        workflow.record_fetch_error_task,
        workflow.discover_tickers_task,
        workflow.prune_snapshots_task,
    )
    assert all(task.timeout_seconds is None for task in tasks)
    assert workflow.collect_position_news_flow.timeout_seconds == (
        workflow._FLOW_TIMEOUT_SECONDS
    )
    assert workflow._FLOW_TIMEOUT_SECONDS >= 90


def test_flow_skips_late_scheduled_run_before_discovery(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_COLLECTION_SECONDS", "60")
    monkeypatch.setenv("POSITION_NEWS_MAX_SCHEDULE_LAG_SECONDS", "60")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 61.9)
    monkeypatch.setattr(
        workflow,
        "discover_tickers_task",
        lambda: pytest.fail("late run must not discover tickers"),
    )

    summary = workflow.collect_position_news_flow.fn()

    assert summary["run_status"] == "skipped_late"
    assert summary["schedule_lag_seconds"] == 61
    assert summary["ticker_count"] == 0
    assert summary["items"] == []


def test_flow_opens_source_circuit_and_skips_remaining_tickers(monkeypatch):
    monkeypatch.setenv(
        "POSITION_NEWS_MAX_CONSECUTIVE_FETCH_FAILURES",
        "2",
    )
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(
        workflow,
        "discover_tickers_task",
        lambda: ["BTC", "ETH", "SOL"],
    )

    fetches = []
    records = []

    def fail_fetch(symbol):
        fetches.append(symbol)
        raise RuntimeError("rss down")

    monkeypatch.setattr(
        workflow,
        "fetch_ticker_news_task",
        Submitter(fail_fetch),
    )
    monkeypatch.setattr(
        workflow,
        "record_fetch_error_task",
        Submitter(lambda *args: records.append(args)),
    )
    monkeypatch.setattr(
        workflow,
        "process_ticker_news_task",
        Submitter(lambda *_args: pytest.fail("process must not run")),
    )
    monkeypatch.setattr(
        workflow,
        "prune_snapshots_task",
        Submitter(lambda _days: 0),
    )

    captured = {}
    real_summarize = workflow.collector.summarize_results

    def capture_summary(results, *, removed=0):
        summary = real_summarize(results, removed=removed)
        captured["summary"] = summary
        return summary

    monkeypatch.setattr(
        workflow.collector,
        "summarize_results",
        capture_summary,
    )

    with pytest.raises(
        workflow.NewsSourceCircuitOpen,
        match="Google News RSS",
    ):
        workflow.collect_position_news_flow.fn()

    assert fetches == ["BTC", "ETH"]
    assert records == [
        ("BTC", "rss down"),
        ("ETH", "rss down"),
    ]
    assert captured["summary"]["error"] == 2
    assert captured["summary"]["skipped"] == 1
    assert captured["summary"]["items"][-1]["asset_symbol"] == "SOL"
    assert captured["summary"]["items"][-1]["reason"] == "source_circuit_open"


def test_flow_publishes_all_rss_before_browser_tasks_and_holds_leases(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: ["BTC", "ETH"])
    events = []
    active = set()
    def claim(symbol):
        active.add(symbol)
        return symbol
    def finish(symbol, token):
        assert token == symbol
        active.remove(symbol)
    monkeypatch.setattr(workflow.repository, "claim_collection", claim)
    monkeypatch.setattr(workflow.repository, "finish_collection", finish)
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", Submitter(
        lambda symbol: events.append(("rss", symbol)) or _payload(symbol)))
    monkeypatch.setattr(workflow, "publish_initial_news_task", Submitter(
        lambda symbol, payload: events.append(("published", symbol)) or {
            "asset_symbol": symbol, "status": "stored", "used_ai_budget": False}))
    def browser(symbol, payload):
        assert symbol in active
        assert ("published", "BTC") in events and ("published", "ETH") in events
        events.append(("browser", symbol))
        return payload
    monkeypatch.setattr(workflow, "enrich_ticker_news_task", Submitter(browser))
    monkeypatch.setattr(workflow, "process_ticker_news_task", Submitter(
        lambda symbol, payload, ai: events.append(("process", symbol)) or {
            "asset_symbol": symbol, "status": "stored", "used_ai_budget": False}))
    monkeypatch.setattr(workflow, "prune_snapshots_task", Submitter(lambda _: 0))
    result = workflow.collect_position_news_flow.fn()
    assert events == [("rss", "BTC"), ("published", "BTC"), ("rss", "ETH"),
                      ("published", "ETH"), ("browser", "BTC"), ("process", "BTC"),
                      ("browser", "ETH"), ("process", "ETH")]
    assert active == set()
    assert result["configuration"]["browser_enabled"] is True


def test_prefect_browser_can_recover_when_rss_is_down(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: ["BTC"])
    def fail(*_):
        raise RuntimeError("RSS unavailable")
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", Submitter(fail))
    monkeypatch.setattr(workflow, "record_fetch_error_task", Submitter(lambda *_: None))
    def browser(symbol, payload):
        assert payload["items"] == []
        assert payload["sources"][0]["status"] == "error"
        return _payload(symbol)
    monkeypatch.setattr(workflow, "enrich_ticker_news_task", Submitter(browser))
    monkeypatch.setattr(workflow, "process_ticker_news_task", Submitter(
        lambda symbol, payload, ai: {"asset_symbol": symbol, "status": "stored", "used_ai_budget": False}))
    monkeypatch.setattr(workflow, "prune_snapshots_task", Submitter(lambda _: 0))
    assert workflow.collect_position_news_flow.fn()["stored"] == 1


def test_rolling_deployment_does_not_pause_new_worker_schedule(monkeypatch):
    monkeypatch.setenv("PREFECT_API_URL", "https://prefect.example.test/api")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "revision-under-test")
    monkeypatch.setenv("POSITION_NEWS_SCHEDULE_SECONDS", "60")
    monkeypatch.setenv("POSITION_NEWS_COLLECTION_SECONDS", "300")
    monkeypatch.setattr("sys.argv", ["position_news", "serve"])
    monkeypatch.setattr(workflow.repository, "assert_worker_database", lambda: None)
    monkeypatch.setattr(workflow, "init_db", lambda: None)
    options = {}
    monkeypatch.setattr(workflow.collect_position_news_flow, "serve", lambda **kwargs: options.update(kwargs))
    workflow.main()
    assert options["pause_on_shutdown"] is False
    assert options["paused"] is False
    assert options["version"] == "revision-under-test"
    assert options["interval"].total_seconds() == 60
    assert options["limit"] == options["global_limit"] == 1


def test_enrichment_task_fails_visibly_with_retained_rss_payload(monkeypatch):
    payload = {**_payload("BTC"), "browser_enrichment": {
        "status": "error", "source_count": 8, "successful_sources": 0},
        "sources": [{"name": "public-browser-source", "status": "error", "error": "TimeoutError"}]}
    monkeypatch.setattr(workflow.collector, "enrich_payload", lambda *_: payload)
    with pytest.raises(workflow.BrowserEnrichmentUnavailable, match="Playwright 전체 소스") as failure:
        workflow.enrich_ticker_news_task.fn("BTC", _payload("BTC"))
    assert failure.value.payload == payload
    assert failure.value.payload["items"] == _payload("BTC")["items"]


@pytest.mark.parametrize("status,successful", [("partial", 3), ("disabled", 0)])
def test_partial_or_disabled_browser_does_not_fail_task(monkeypatch, caplog, status, successful):
    payload = {**_payload("BTC"), "browser_enrichment": {
        "status": status, "source_count": 8, "successful_sources": successful}}
    monkeypatch.setattr(workflow.collector, "enrich_payload", lambda *_: payload)
    assert workflow.enrich_ticker_news_task.fn("BTC", _payload("BTC")) == payload
    if status == "partial":
        assert "성공 3/8" in caplog.text
    else:
        assert not caplog.records


def test_flow_processes_rss_once_before_failing_browser_outage(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0.0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: ["BTC"])
    payload = _payload("BTC")
    events = []
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", Submitter(lambda _: payload))
    monkeypatch.setattr(workflow, "publish_initial_news_task", Submitter(
        lambda *_: events.append("rss_published") or {"status": "stored", "used_ai_budget": False}))
    failed_payload = {**payload, "browser_enrichment": {
        "status": "error", "source_count": 8, "successful_sources": 0}}
    def browser(*_):
        events.append("browser_failed")
        raise workflow.BrowserEnrichmentUnavailable("Playwright sources unavailable", failed_payload)
    monkeypatch.setattr(workflow, "enrich_ticker_news_task", Submitter(browser))
    def process(symbol, received, allow_ai):
        assert symbol == "BTC" and received == failed_payload and allow_ai is True
        events.append("final_processed")
        return {"asset_symbol": symbol, "status": "stored", "used_ai_budget": True, "browser_status": "error"}
    monkeypatch.setattr(workflow, "process_ticker_news_task", Submitter(process))
    monkeypatch.setattr(workflow.repository, "finish_collection", lambda *_: events.append("lease_released"))
    monkeypatch.setattr(workflow, "prune_snapshots_task", Submitter(lambda _: events.append("pruned") or 0))
    with pytest.raises(workflow.BrowserEnrichmentUnavailable, match="RSS 결과를 유지") as failure:
        workflow.collect_position_news_flow.fn()
    assert events == ["rss_published", "browser_failed", "final_processed", "lease_released", "pruned"]
    summary = failure.value.payload["summary"]
    assert summary["stored"] == 1
    assert summary["ai_budget_used"] == 1
    assert summary["browser_failed_tickers"] == ["BTC"]
    assert summary["browser_failed_count"] == 1
    assert summary["run_status"] == "browser_unavailable"


def test_effective_browser_concurrency_matches_render_default_and_bounds(monkeypatch):
    monkeypatch.delenv("POSITION_NEWS_BROWSER_CONCURRENCY", raising=False)
    monkeypatch.delenv("RENDER", raising=False)
    assert workflow.effective_config()["browser_concurrency"] == 3
    monkeypatch.setenv("RENDER", "true")
    assert workflow.effective_config()["browser_concurrency"] == 1
    monkeypatch.setenv("POSITION_NEWS_BROWSER_CONCURRENCY", "9")
    assert workflow.effective_config()["browser_concurrency"] == 4
