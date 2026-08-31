"""Prefect adapter tests call the underlying flow function with local fakes."""
from __future__ import annotations

import pytest

pytest.importorskip("sqlmodel")
pytest.importorskip("prefect")

from app.workflows import position_news as workflow


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


def test_prefect_retry_and_timeout_metadata():
    assert workflow.fetch_ticker_news_task.retries == 2
    assert workflow.fetch_ticker_news_task.retry_delay_seconds == [5, 15]
    assert workflow.process_ticker_news_task.retries == 0
    assert workflow.collect_position_news_flow.retries == 0

    tasks = (
        workflow.fetch_ticker_news_task,
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
