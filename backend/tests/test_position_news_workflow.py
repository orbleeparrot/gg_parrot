"""Prefect adapter tests call the underlying flow function with local fakes."""
from __future__ import annotations

import json

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
    options, deployments = {}, {}
    monkeypatch.setattr(workflow.collect_position_news_flow, "to_deployment",
                        lambda **kwargs: deployments.setdefault("collection", kwargs))
    monkeypatch.setattr(workflow.coindesk_source_probe_flow, "to_deployment",
                        lambda **kwargs: deployments.setdefault("probe", kwargs))
    def serve(*registered, **kwargs):
        options.update(kwargs)
        assert registered == (deployments["collection"], deployments["probe"])
    monkeypatch.setattr(workflow, "serve", serve)
    workflow.main()
    assert options["pause_on_shutdown"] is False
    assert options["limit"] == 1  # Both deployments share the browser worker slot.
    assert deployments["collection"]["paused"] is False
    assert deployments["collection"]["interval"].total_seconds() == 60
    assert deployments["collection"]["name"] == "shared-ticker-news"
    assert deployments["probe"]["name"] == "coindesk-source-probe"
    assert not {"interval", "cron", "rrule", "schedule", "schedules"} & deployments["probe"].keys()
    for deployment in deployments.values():
        assert deployment["version"] == "revision-under-test"
        assert deployment["concurrency_limit"] == 1
        assert deployment["parameters"] == {"browser_budget_seconds": 90}


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


def test_browser_source_logs_distinguish_navigation_cooldown_and_unattempted_queue(monkeypatch, capsys):
    sources = [
        {"name": "coindesk_rss", "status": "ready", "fetched_count": 25, "item_count": 1},
        {"name": "coindesk_asset_topic", "source_type": "coindesk_topic_playwright",
         "source_page": "https://www.coindesk.com/tag/bitcoin", "scope": "bitcoin",
         "status": "error", "fetched_count": 0, "item_count": 0, "cached": False,
         "attempted": True, "http_status": 429, "phase": "navigation", "error": "rate_limited",
         "response_url": "https://www.coindesk.com/tag/bitcoin",
         "response_headers": {"server": "cloudflare", "retry-after": "300"},
         "retry_at": 1_800_000_300, "queue_ms": 5, "elapsed_ms": 120,
         "timings_ms": {"navigation": 100}},
        {"name": "coindesk_section_markets", "source_type": "coindesk_section_playwright",
         "source_page": "https://www.coindesk.com/markets", "status": "error",
         "fetched_count": 0, "item_count": 0, "cached": True, "attempted": False,
         "http_status": 429, "phase": "publisher_cooldown", "error": "rate_limited",
         "retry_at": 1_800_000_300},
        {"name": "coindesk_asset_search_0", "source_type": "coindesk_asset_search_playwright",
         "source_page": "https://www.coindesk.com/search/", "search_term": "bitcoin",
         "status": "error", "fetched_count": 0, "item_count": 0, "attempted": False,
         "phase": "queue", "error": "budget_exhausted", "queue_ms": 35_000},
        {"name": "decrypt_news", "source_type": "decrypt_section_playwright",
         "source_page": "https://decrypt.co/news", "status": "ready", "fetched_count": 20,
         "item_count": 2, "attempted": True,
         "pagination": {"initial_count": 10, "final_count": 20, "clicks": 1,
                        "pages_loaded": 2, "stop_reason": "click_limit"}},
    ]
    payload = {**_payload("BTC"), "sources": sources, "browser_enrichment": {
        "status": "partial", "source_count": 4, "successful_sources": 1}}
    monkeypatch.setattr(workflow.collector, "enrich_payload", lambda *_: payload)
    assert workflow.enrich_ticker_news_task.fn("BTC", _payload("BTC")) == payload
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert logs[0]["sources"] == sources  # Existing aggregate report stays available.
    events = [entry for entry in logs if entry.get("event") == "browser_source"]
    assert len(events) == 4
    assert [event["name"] for event in events] == [source["name"] for source in sources[1:]]
    assert all(event["asset_symbol"] == "BTC" for event in events)
    assert events[0]["attempted"] is True and events[0]["http_status"] == 429
    assert events[0]["response_headers"] == {"server": "cloudflare", "retry-after": "300"}
    assert events[0]["timings_ms"] == {"navigation": 100}
    assert events[1]["attempted"] is False and events[1]["phase"] == "publisher_cooldown"
    assert events[1]["retry_at"] == events[0]["retry_at"]
    assert events[2]["phase"] == "queue" and events[2]["queue_ms"] == 35_000
    assert "http_status" not in events[2]  # A queued source received no HTTP response.
    assert events[3]["pagination"]["final_count"] == 20


def test_legacy_browser_source_logs_do_not_invent_attempt_or_http_status(monkeypatch, capsys):
    payload = {**_payload("BTC"), "browser_enrichment": {
        "status": "error", "source_count": 1, "successful_sources": 0},
        "sources": [{"name": "coindesk_asset_topic", "source_type": "coindesk_topic_playwright",
                     "status": "error", "error": "TimeoutError", "phase": "queue"}]}
    monkeypatch.setattr(workflow.collector, "enrich_payload", lambda *_: payload)
    with pytest.raises(workflow.BrowserEnrichmentUnavailable):
        workflow.enrich_ticker_news_task.fn("BTC", _payload("BTC"))
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    event = next(entry for entry in logs if entry.get("event") == "browser_source")
    assert event["phase"] == "queue"
    assert "attempted" not in event and "http_status" not in event


def test_effective_browser_budgets_use_collector_helpers(monkeypatch):
    monkeypatch.setattr(workflow.news_mod, "_browser_batch_budget_seconds", lambda: 100.5)
    monkeypatch.setattr(workflow.news_mod, "_browser_page_budget_seconds", lambda: 12.5)
    monkeypatch.setattr(workflow.news_mod, "_browser_max_load_more_clicks", lambda: 4)
    config = workflow.effective_config()
    assert config["browser_budget_seconds"] == 100.5
    assert config["browser_page_budget_seconds"] == 12.5
    assert config["browser_max_load_more_clicks"] == 4


def test_manual_probe_reads_eight_fixed_sources_without_positions_or_ai(monkeypatch, capsys):
    expected_urls = {"https://www.coindesk.com/" + path for path in (
        "markets", "policy", "tech", "business", "tag/bitcoin", "tag/ethereum", "tag/ripple", "tag/solana")}
    def cache(descriptors):
        assert len(descriptors) == 8
        assert {page["url"] for page in descriptors} == expected_urls
        return {workflow.news_mod._browser_page_key(page): {
            "status": "ready" if index else "empty", "items": [{"title": "Public article"}] if index else [],
            "attempted": bool(index), "cached": not index, "http_status": 200,
        } for index, page in enumerate(descriptors)}
    def forbidden(*_args, **_kwargs):
        pytest.fail("A public source probe must not access positions, RSS, or AI processing")
    monkeypatch.setattr(workflow.news_mod, "_cached_browser_pages", cache)
    monkeypatch.setattr(workflow, "discover_tickers_task", forbidden)
    monkeypatch.setattr(workflow.news_mod, "fetch_coin_news_for_collector", forbidden)
    monkeypatch.setattr(workflow.collector, "collect_payload", forbidden)
    result = workflow.coindesk_source_probe_flow.fn()
    assert result["status"] == "ready" and result["successful_sources"] == result["source_count"] == 8
    assert result["sources"][0]["status"] == "empty" and result["sources"][0]["item_count"] == 0
    logs = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    events = [entry for entry in logs if entry.get("event") == "browser_source"]
    assert len(events) == 8 and {entry["source_page"] for entry in events} == expected_urls
    assert all(entry["asset_symbol"] is None for entry in events)
    assert result["configuration"]["collector_mode"] == "browser_source_probe"


@pytest.mark.parametrize("failed_count", [1, 8])
def test_manual_probe_reports_every_source_before_failing_partial_or_complete_outage(monkeypatch, capsys, failed_count):
    def cache(descriptors):
        return {workflow.news_mod._browser_page_key(page): (
            {"status": "error", "items": [], "attempted": False, "cached": True,
             "http_status": 429, "phase": "publisher_cooldown", "error": "rate_limited"}
            if index < failed_count else {"status": "ready", "items": [{"title": "Public article"}]}
        ) for index, page in enumerate(descriptors)}
    monkeypatch.setattr(workflow.news_mod, "_cached_browser_pages", cache)
    with pytest.raises(workflow.BrowserEnrichmentUnavailable, match=f"{failed_count}/8") as failure:
        workflow.coindesk_source_probe_flow.fn()
    assert failure.value.payload["successful_sources"] == 8 - failed_count
    assert len(failure.value.payload["sources"]) == 8
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert sum(entry.get("event") == "browser_source" for entry in events) == 8
    assert events[-1]["status"] == ("error" if failed_count == 8 else "partial")


def test_explicit_run_budget_overrides_render_environment_without_changing_default(monkeypatch):
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("POSITION_NEWS_BROWSER_BUDGET_SECONDS", "35")
    assert workflow.effective_config()["browser_budget_seconds"] == 35
    assert workflow.effective_config(90)["browser_budget_seconds"] == 90
    assert workflow.effective_config(999)["browser_budget_seconds"] == 180
    assert workflow.effective_config()["browser_budget_seconds"] == 35


def test_manual_probe_passes_deployment_budget_to_page_collection(monkeypatch):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_BUDGET_SECONDS", "35")
    calls = []
    def cache(descriptors, *, budget_seconds):
        calls.append(budget_seconds)
        return {workflow.news_mod._browser_page_key(page): {"status": "empty", "items": [], "http_status": 200}
                for page in descriptors}
    monkeypatch.setattr(workflow.news_mod, "_cached_browser_pages", cache)
    result = workflow.coindesk_source_probe_flow.fn(browser_budget_seconds=90)
    assert calls == [90]
    assert result["configuration"]["browser_budget_seconds"] == 90
    assert result["status"] == "ready"
    assert workflow.coindesk_source_probe_flow.timeout_seconds == 210


@pytest.mark.parametrize("requested,expected", [(90, 90), (999, 180)])
def test_main_flow_passes_budget_through_task_and_collector_after_initial_rss(monkeypatch, requested, expected):
    monkeypatch.setenv("POSITION_NEWS_BROWSER_BUDGET_SECONDS", "35")
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    monkeypatch.setenv("POSITION_NEWS_MAX_AI_ANALYSES_PER_RUN", "0")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: ["CHIP"])
    events = []
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", Submitter(
        lambda symbol: events.append("rss") or _payload(symbol)))
    monkeypatch.setattr(workflow, "publish_initial_news_task", Submitter(
        lambda *_: events.append("published") or {"status": "stored", "used_ai_budget": False}))
    def enrich(symbol, payload, *, browser_budget_seconds):
        assert symbol == "CHIP" and browser_budget_seconds == expected
        events.append("browser")
        return {**payload, "browser_enrichment": {"status": "ready", "source_count": 1, "successful_sources": 1}}
    monkeypatch.setattr(workflow.news_mod, "enrich_coin_news_for_collector", enrich)
    monkeypatch.setattr(workflow, "enrich_ticker_news_task", Submitter(workflow.enrich_ticker_news_task.fn))
    def process(symbol, payload, allow_ai):
        assert payload["items"] == _payload("CHIP")["items"] and allow_ai is False
        events.append("processed")
        return {"asset_symbol": symbol, "status": "stored", "used_ai_budget": False}
    monkeypatch.setattr(workflow, "process_ticker_news_task", Submitter(process))
    monkeypatch.setattr(workflow, "prune_snapshots_task", Submitter(lambda _: 0))
    summary = workflow.collect_position_news_flow.fn(browser_budget_seconds=requested)
    assert summary["configuration"]["browser_budget_seconds"] == expected
    assert events == ["rss", "published", "browser", "processed"]


@pytest.mark.parametrize("elapsed,deferred", [(230, True), (135.001, True), (135, False)])
def test_flow_reserves_full_browser_budget_and_processing_time_after_initial_rss(monkeypatch, elapsed, deferred):
    monkeypatch.setenv("POSITION_NEWS_MAX_CYCLE_SECONDS", "240")
    monkeypatch.setenv("POSITION_NEWS_BROWSER_ENRICHMENT_ENABLED", "true")
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0)
    monkeypatch.setattr(workflow, "discover_tickers_task", lambda: ["BTC"])
    clock, events, releases = [0], [], []
    monkeypatch.setattr(workflow.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(workflow, "fetch_ticker_news_task", Submitter(lambda symbol: _payload(symbol)))
    initial = {"asset_symbol": "BTC", "status": "stored", "used_ai_budget": False, "snapshot_key": "published-rss"}
    def publish(*_):
        events.append("rss_published")
        clock[0] = elapsed
        return initial
    monkeypatch.setattr(workflow, "publish_initial_news_task", Submitter(publish))
    def browser(symbol, payload, budget):
        assert not deferred and budget == 90
        events.append("browser")
        return payload
    def process(symbol, payload, allow_ai):
        assert not deferred
        events.append("processed")
        return initial
    monkeypatch.setattr(workflow, "enrich_ticker_news_task", Submitter(browser))
    monkeypatch.setattr(workflow, "process_ticker_news_task", Submitter(process))
    monkeypatch.setattr(workflow.repository, "finish_collection",
                        lambda symbol, token, **kwargs: releases.append((symbol, token, kwargs)))
    monkeypatch.setattr(workflow, "prune_snapshots_task", Submitter(lambda _: 0))
    result = workflow.collect_position_news_flow.fn(browser_budget_seconds=90)
    assert result["stored"] == 1 and result["items"][0]["snapshot_key"] == "published-rss"
    if deferred:
        assert events == ["rss_published"]
        assert result["items"][0]["reason"] == "browser_budget_deferred"
        assert result["items"][0]["browser_status"] == "deferred"
        assert releases == [("BTC", "token", {"next_delay_seconds": 60})]
    else:
        assert events == ["rss_published", "browser", "processed"]
        assert releases == [("BTC", "token", {})]
