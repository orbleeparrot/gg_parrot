"""Prefect adapter contracts without the orchestration API or provider calls."""
import json

import pytest
from prefect.flows import load_flow_from_entrypoint
from prefect.types.entrypoint import EntrypointType

from app.workflows import whale_activity as workflow


class Immediate:
    def __init__(self, value):
        self.value = value

    def result(self):
        return self.value


class Submitter:
    def __init__(self, fn):
        self.fn = fn

    def submit(self, *args):
        return Immediate(self.fn(*args))


@pytest.fixture(autouse=True)
def no_external_work(monkeypatch):
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 0)
    monkeypatch.setattr(workflow.repository, "prune_inactive_states", lambda: 0)


def test_inactive_flow_does_not_submit_source(monkeypatch):
    monkeypatch.setattr(workflow, "discover_pairs_task", lambda: [])
    monkeypatch.setattr(workflow, "collect_pair_task", Submitter(lambda *_: pytest.fail("no active sessions")))
    result = workflow.collect_whale_activity_flow.fn()
    assert result["active_pair_count"] == result["checked_pair_count"] == 0
    assert result["ai_calls"] == 0


def test_flow_keeps_markets_distinct_and_surfaces_source_failures(monkeypatch):
    pairs = [{"symbol": "BTCUSDT", "market": "futures"}, {"symbol": "BTCUSDT", "market": "spot"}]
    monkeypatch.setattr(workflow, "discover_pairs_task", lambda: pairs)
    calls = []
    def collect(symbol, market):
        calls.append((symbol, market))
        return {"symbol": symbol, "market": market, "status": "error" if market == "futures" else "empty"}
    monkeypatch.setattr(workflow, "collect_pair_task", Submitter(collect))
    with pytest.raises(workflow.WhaleCollectionUnavailable):
        workflow.collect_whale_activity_flow.fn()
    assert calls == [("BTCUSDT", "futures"), ("BTCUSDT", "spot")]


def test_late_run_never_replays_old_collection(monkeypatch):
    monkeypatch.setattr(workflow, "_schedule_lag_seconds", lambda: 121)
    monkeypatch.setattr(workflow, "discover_pairs_task", lambda: pytest.fail("late flow"))
    assert workflow.collect_whale_activity_flow.fn()["status"] == "skipped_late"


def test_flow_reserves_one_http_call_budget_before_next_pair(monkeypatch):
    monkeypatch.setattr(workflow, "discover_pairs_task", lambda: [
        {"symbol": "BTCUSDT", "market": "spot"}, {"symbol": "ETHUSDT", "market": "spot"}])
    ticks = iter([0, 0, 13])
    monkeypatch.setattr(workflow.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(workflow, "collect_pair_task", Submitter(lambda *args: {"status": "empty"}))
    result = workflow.collect_whale_activity_flow.fn()
    assert result["checked_pair_count"] == 1
    assert result["deferred_pair_count"] == 1


def test_deployment_is_scheduled_versioned_and_importable(monkeypatch):
    monkeypatch.setenv("RENDER_GIT_COMMIT", "revision-under-test")
    deployment = workflow.create_deployment()
    assert deployment.name == "shared-whale-trades"
    assert deployment.paused is False and deployment.concurrency_limit == 1
    assert deployment.version == "revision-under-test"
    assert deployment.schedules[0].schedule.interval.total_seconds() == 30
    assert deployment.entrypoint_type == EntrypointType.MODULE_PATH
    assert load_flow_from_entrypoint(deployment.entrypoint) is workflow.collect_whale_activity_flow
    assert workflow.collect_pair_task.retries == workflow.collect_whale_activity_flow.retries == 0


def test_source_log_is_operational_metadata_only(monkeypatch, capsys):
    result = {"symbol": "CHIPUSDT", "market": "futures", "status": "empty", "sampled_trades": 500,
              "large_trade_count": 0, "http_status": 200, "elapsed_ms": 382}
    monkeypatch.setattr(workflow.collector, "collect_pair", lambda *_: result)
    assert workflow.collect_pair_task.fn("CHIPUSDT", "futures") == result
    log = json.loads(capsys.readouterr().out)
    assert log == {"event": "whale_source", **result}
