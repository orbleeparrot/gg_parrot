"""Independent Prefect slots share one process; no Prefect API is contacted."""
import asyncio
import signal
import threading
from types import SimpleNamespace

import pytest

from app.workflows import agent_collectors


def _signals(monkeypatch):
    previous = {signal.SIGTERM: object(), signal.SIGINT: object()}
    handlers = dict(previous)
    # Return the real previous handler contract without installing OS handlers.
    def install(number, handler):
        old = handlers[number]
        handlers[number] = handler
        return old
    monkeypatch.setattr(signal, "signal", install)
    return handlers, previous


def _runner_factory(*, on_start, on_stop=None):
    instances = []

    class FakeRunner:
        def __init__(self, **kwargs):
            self.settings = kwargs
            self.deployments = []
            self.started = False
            self.stop_calls = 0
            self.finished = False
            self.closed = asyncio.Event()
            instances.append(self)

        async def aadd_deployment(self, deployment):
            self.deployments.append(deployment)

        async def start(self):
            self.started = True
            self.thread_id = threading.get_ident()
            self.loop = asyncio.get_running_loop()
            try:
                await on_start(self, instances)
                await self.closed.wait()
            finally:
                self.started = False
                self.finished = True

        async def astop(self):
            self.stop_calls += 1
            if on_stop is not None:
                await on_stop(self)
            self.closed.set()

    return FakeRunner, instances


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_both_slots_start_on_one_background_loop_and_signal_stops_both(monkeypatch, signum):
    handlers, previous = _signals(monkeypatch)
    async def on_start(runner, instances):
        if runner.deployments == ["whale"]:
            assert instances[0].started  # News is already running and does not block the whale slot.
            handlers[signum](signum, None)
    factory, runners = _runner_factory(on_start=on_start)
    agent_collectors.serve_collectors(["news", "probe"], ["whale"],
        runner_factory=factory, poll_seconds=.001, shutdown_seconds=1)
    assert [runner.deployments for runner in runners] == [["news", "probe"], ["whale"]]
    assert all(runner.settings["limit"] == 1 and runner.settings["pause_on_shutdown"] is False for runner in runners)
    assert runners[1].settings["query_seconds"] == 5
    assert runners[0].thread_id == runners[1].thread_id != threading.get_ident()
    assert runners[0].loop is runners[1].loop
    assert all(runner.stop_calls == 1 and runner.finished for runner in runners)
    assert handlers == previous


def test_runner_failure_stops_other_runner_and_propagates(monkeypatch):
    handlers, previous = _signals(monkeypatch)
    async def on_start(runner, instances):
        if runner.deployments == ["whale"]:
            raise RuntimeError("whale poller failed")
    factory, runners = _runner_factory(on_start=on_start)
    with pytest.raises(RuntimeError, match="whale poller failed"):
        agent_collectors.serve_collectors(["news", "probe"], ["whale"],
            runner_factory=factory, poll_seconds=.001, shutdown_seconds=1)
    assert runners[0].stop_calls == 1
    assert all(runner.finished for runner in runners)
    assert handlers == previous


def test_unexpected_clean_runner_exit_is_a_worker_failure(monkeypatch):
    _signals(monkeypatch)
    async def on_start(runner, instances):
        if runner.deployments == ["whale"]:
            runner.closed.set()
    factory, runners = _runner_factory(on_start=on_start)
    with pytest.raises(RuntimeError, match="exited"):
        agent_collectors.serve_collectors(["news"], ["whale"],
            runner_factory=factory, poll_seconds=.001, shutdown_seconds=1)
    assert runners[0].stop_calls == 1 and runners[0].finished


def test_shutdown_timeout_cancels_remaining_runner_tasks(monkeypatch):
    handlers, previous = _signals(monkeypatch)
    async def on_start(runner, instances):
        if runner.deployments == ["whale"]:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
    async def on_stop(runner):
        await asyncio.Event().wait()
    factory, runners = _runner_factory(on_start=on_start, on_stop=on_stop)
    with pytest.raises(RuntimeError, match="shutdown"):
        agent_collectors.serve_collectors(["news"], ["whale"],
            runner_factory=factory, poll_seconds=.001, shutdown_seconds=.1)
    assert handlers == previous


def test_registration_failure_does_not_attempt_stop_before_start(monkeypatch):
    handlers, previous = _signals(monkeypatch)
    async def on_start(runner, instances):
        pass
    factory, runners = _runner_factory(on_start=on_start)
    original = factory.aadd_deployment
    async def register(self, deployment):
        if deployment == "whale":
            raise RuntimeError("deployment registration failed")
        await original(self, deployment)
    monkeypatch.setattr(factory, "aadd_deployment", register)
    with pytest.raises(RuntimeError, match="deployment registration failed"):
        agent_collectors.serve_collectors(["news"], ["whale"],
            runner_factory=factory, poll_seconds=.001, shutdown_seconds=1)
    assert runners[0].stop_calls == 1 and runners[1].stop_calls == 0
    assert handlers == previous


def test_existing_cli_registers_all_deployments_in_shared_runner_supervisor(monkeypatch):
    from app.workflows import position_news, whale_activity

    monkeypatch.setenv("PREFECT_API_URL", "https://prefect.invalid")
    monkeypatch.setenv("WHALE_TRADE_PREFECT_ENABLED", "true")
    monkeypatch.setattr("sys.argv", ["position_news", "serve"])
    monkeypatch.setattr(position_news.repository, "assert_worker_database", lambda: None)
    monkeypatch.setattr(position_news, "init_db", lambda: None)
    monkeypatch.setattr(position_news, "effective_config", lambda: {})
    deployments = []
    def deployment(**kwargs):
        result = SimpleNamespace(**kwargs)
        deployments.append(result)
        return result
    monkeypatch.setattr(position_news.collect_position_news_flow, "to_deployment", deployment)
    monkeypatch.setattr(position_news.coindesk_source_probe_flow, "to_deployment", deployment)
    monkeypatch.setattr(whale_activity, "create_deployment", lambda: "whale")
    monkeypatch.setattr(position_news, "serve", lambda *_args, **_kwargs: pytest.fail("news-only serve"))
    served = []
    monkeypatch.setattr(agent_collectors, "serve_collectors", lambda news, whales: served.append((news, whales)))
    position_news.main()
    assert served == [(deployments, ["whale"])]
    assert [item.entrypoint for item in deployments] == [
        "app.workflows.position_news.collect_position_news_flow",
        "app.workflows.position_news.coindesk_source_probe_flow",
    ]
