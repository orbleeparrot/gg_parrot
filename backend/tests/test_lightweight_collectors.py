"""Scheduled records stay in Prefect while lightweight execution stays in one thread."""
import asyncio
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from prefect.client.schemas.objects import FlowRun
from prefect.states import Cancelling, Completed, Failed, Pending, Scheduled

from app.workflows.lightweight_collectors import LightweightCollectorRunner, POLL_LIMIT

TRADE = "app.workflows.whale_activity.collect_whale_activity_flow"
HOLDERS = "app.workflows.onchain_holders.collect_onchain_holders_flow"


def scheduled(deployment_id, *, age=10, **changes):
    stamp = datetime.now(timezone.utc)-timedelta(seconds=age)
    values = dict(id=uuid4(), flow_id=uuid4(), deployment_id=deployment_id,
                  state=Scheduled(scheduled_time=stamp), expected_start_time=stamp,
                  next_scheduled_start_time=stamp, auto_scheduled=True)
    values.update(changes)
    return FlowRun(**values)


class Client:
    def __init__(self):
        self.runs = []
        self.extra_runs = []
        self.polls = []
        self.filtered_queries = []
        self.transitions = []
        self.claim_status = "ACCEPT"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get_scheduled_flow_runs_for_deployments(self, **kwargs):
        self.polls.append(kwargs)
        return self.runs

    async def read_flow_runs(self, **kwargs):
        self.filtered_queries.append(kwargs)
        return self.extra_runs

    async def set_flow_run_state(self, identity, state, force=False):
        self.transitions.append((identity, state, force))
        if self.claim_status == "ACCEPT":
            self.runs = [row.model_copy(update={"state": state}) if row.id == identity else row for row in self.runs]
            self.extra_runs = [row.model_copy(update={"state": state}) if row.id == identity else row for row in self.extra_runs]
        return SimpleNamespace(status=self.claim_status, state=state)

    async def read_flow_run(self, identity):
        return next(row for row in [*self.runs, *self.extra_runs] if row.id == identity)


async def runner(client, engine=None):
    executed, retired = [], []
    def execute(**kwargs):
        executed.append((kwargs, threading.get_ident()))
        return engine(**kwargs) if engine else Completed()
    async def retire(_client, row, **kwargs):
        retired.append(row.id)
        return {"outcome": "retired"}
    result = LightweightCollectorRunner(client_factory=lambda: client, engine_runner=execute,
        retire_stale=retire, flow_resolver=lambda entrypoint: entrypoint)
    identities = [uuid4(), uuid4()]
    for identity, entrypoint in zip(identities, [TRADE, HOLDERS]):
        async def apply(identity=identity):
            return identity
        assert await result.aadd_deployment(SimpleNamespace(entrypoint=entrypoint, aapply=apply)) == identity
    return result, identities, executed, retired


def test_known_deployments_use_existing_run_id_and_default_pending_claim_in_other_thread():
    async def scenario():
        client = Client()
        lane, ids, executed, _ = await runner(client)
        run = scheduled(ids[0])
        client.runs = [run]
        await lane._poll(client)
        assert client.polls[0]["deployment_ids"] == ids
        assert client.transitions[0][0] == run.id
        assert client.transitions[0][1].name == "Pending" and client.transitions[0][2] is False
        kwargs, thread_id = executed[0]
        assert kwargs["flow"] == TRADE and kwargs["flow_run"].id == run.id
        assert kwargs["flow_run"].state.is_pending() and kwargs["return_type"] == "state"
        assert thread_id != threading.get_ident()
    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["ABORT", "WAIT", "REJECT"])
def test_rejected_or_waiting_claim_never_runs_engine(status):
    async def scenario():
        client = Client()
        lane, ids, executed, _ = await runner(client)
        client.runs = [scheduled(ids[0])]
        client.claim_status = status
        await lane._poll(client)
        assert executed == []
    asyncio.run(scenario())


def test_obsolete_run_is_retired_before_engine_and_does_not_starve_new_deployment():
    async def scenario():
        client = Client()
        lane, ids, executed, retired = await runner(client)
        old, current = scheduled(ids[0], age=1000), scheduled(ids[1])
        client.runs = [old, current]
        await lane._poll(client)
        assert [entry[0]["flow_run"].id for entry in executed] == [current.id]
        assert retired == [old.id]
    asyncio.run(scenario())


def test_full_page_of_obsolete_runs_cannot_hide_fresh_second_deployment():
    async def scenario():
        client = Client()
        lane, ids, executed, retired = await runner(client)
        client.runs = [scheduled(ids[0], age=1000+i) for i in range(POLL_LIMIT)]
        current = scheduled(ids[1])
        client.extra_runs = [current]
        await lane._poll(client)
        assert executed[0][0]["flow_run"].id == current.id
        assert len(retired) == 3
        query = client.filtered_queries[0]
        assert query["deployment_filter"].id.any_ == ids
        assert query["flow_run_filter"].next_scheduled_start_time.after_ is not None
    asyncio.run(scenario())


def test_fairness_alternates_due_deployments_and_ignores_remote_or_future_records():
    async def scenario():
        client = Client()
        lane, ids, executed, retired = await runner(client)
        first, second = scheduled(ids[0], age=20), scheduled(ids[1], age=10)
        client.runs = [scheduled(uuid4(), age=1000), scheduled(ids[1], age=-20), first, second]
        await lane._poll(client)
        await lane._poll(client)
        assert [item[0]["flow"] for item in executed] == [TRADE, HOLDERS]
        assert retired == []
    asyncio.run(scenario())


def test_registration_rejects_dynamic_remote_entrypoint_before_applying():
    async def scenario():
        lane = LightweightCollectorRunner()
        with pytest.raises(ValueError, match="Unsupported"):
            await lane.aadd_deployment(SimpleNamespace(entrypoint="evil.module.flow"))
    asyncio.run(scenario())


def test_registration_applies_revision_to_known_flow():
    async def scenario():
        calls = []
        flow = SimpleNamespace(with_options=lambda **kwargs: calls.append(kwargs) or "revision-flow")
        identity = uuid4()
        async def apply():
            return identity
        lane = LightweightCollectorRunner(flow_resolver=lambda _: flow)
        await lane.aadd_deployment(SimpleNamespace(entrypoint=TRADE, version="commit-sha", aapply=apply))
        assert calls == [{"version": "commit-sha"}]
        assert lane._deployments[identity] == "revision-flow"
    asyncio.run(scenario())


def test_normal_failed_flow_does_not_kill_lane_or_create_an_extra_state():
    async def scenario():
        client = Client()
        lane, ids, executed, _ = await runner(client, engine=lambda **_: Failed(message="source unavailable"))
        client.runs = [scheduled(ids[0])]
        await lane._poll(client)
        assert len(executed) == 1 and len(client.transitions) == 1
    asyncio.run(scenario())


def test_run_that_finished_after_poll_is_not_resurrected():
    async def scenario():
        client = Client()
        lane, ids, executed, _ = await runner(client)
        run = scheduled(ids[0])
        client.runs = [run.model_copy(update={"state": Completed(), "run_count": 1})]
        await lane._execute(client, run)
        assert executed == [] and client.transitions == []
    asyncio.run(scenario())


def test_unexpected_engine_exit_closes_only_its_own_claim_with_safe_error(capsys):
    async def scenario():
        client = Client()
        def crash(**_):
            raise RuntimeError("private-token-in-exception")
        lane, ids, executed, _ = await runner(client, engine=crash)
        run = scheduled(ids[0])
        client.runs = [run]
        await lane._poll(client)
        assert len(executed) == 1
        assert [item[0] for item in client.transitions] == [run.id, run.id]
        assert client.transitions[-1][1].is_failed() and client.transitions[-1][2] is False
    asyncio.run(scenario())
    assert "private-token-in-exception" not in capsys.readouterr().out


def test_start_stop_drains_single_inflight_thread_without_new_work():
    async def scenario():
        client = Client()
        entered, release = threading.Event(), threading.Event()
        def engine(**_):
            entered.set()
            assert release.wait(timeout=5)
            return Completed()
        lane, ids, executed, _ = await runner(client, engine=engine)
        client.runs = [scheduled(ids[0]), scheduled(ids[1])]
        serving = asyncio.create_task(lane.start())
        assert await asyncio.to_thread(entered.wait, 2)
        stopping = asyncio.create_task(lane.astop())
        await asyncio.sleep(.02)
        assert not stopping.done() and lane.started
        release.set()
        await asyncio.wait_for(asyncio.gather(serving, stopping), timeout=3)
        assert len(executed) == 1 and not lane.started
    asyncio.run(scenario())


def test_owned_cancelling_run_is_finalized_only_after_engine_exits():
    async def scenario():
        client = Client()
        def engine(**kwargs):
            identity = kwargs["flow_run"].id
            client.runs = [row.model_copy(update={"state": Cancelling()}) if row.id == identity else row for row in client.runs]
            return Cancelling()
        lane, ids, executed, _ = await runner(client, engine=engine)
        run = scheduled(ids[0])
        client.runs = [run]
        await lane._poll(client)
        assert len(executed) == 1
        assert client.transitions[-1][0] == run.id
        assert client.transitions[-1][1].is_cancelled() and client.transitions[-1][2] is False
    asyncio.run(scenario())


def test_api_failure_logs_safe_metadata_and_waits_for_stop(capsys):
    async def scenario():
        client = Client()
        entered = asyncio.Event()
        async def fail(**_):
            entered.set()
            raise RuntimeError("private-prefect-key")
        client.get_scheduled_flow_runs_for_deployments = fail
        lane, _, executed, _ = await runner(client)
        serving = asyncio.create_task(lane.start())
        await asyncio.wait_for(entered.wait(), timeout=1)
        await asyncio.sleep(.02)
        await lane.astop()
        await serving
        assert executed == []
    asyncio.run(scenario())
    output = capsys.readouterr().out
    assert output.count("lightweight_collector_poll_error") == 1
    assert "private-prefect-key" not in output
