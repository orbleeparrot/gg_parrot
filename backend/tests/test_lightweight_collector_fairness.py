"""A blocked deployment must not consume the other collector's polling turn."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from prefect.client.schemas.objects import FlowRun
from prefect.states import Completed, Pending, Scheduled

from app.workflows.lightweight_collectors import LightweightCollectorRunner


@pytest.mark.parametrize("blocked_status", ["WAIT", "REJECT"])
def test_deferred_deployment_claim_does_not_starve_ready_collector(blocked_status):
    async def scenario():
        now = datetime.now(timezone.utc)
        blocked_deployment, ready_deployment = uuid4(), uuid4()

        def scheduled(deployment_id, seconds_ago):
            stamp = now - timedelta(seconds=seconds_ago)
            return FlowRun(
                id=uuid4(), flow_id=uuid4(), deployment_id=deployment_id,
                state=Scheduled(scheduled_time=stamp),
                expected_start_time=stamp, next_scheduled_start_time=stamp,
                auto_scheduled=True,
            )

        blocked = scheduled(blocked_deployment, 20)
        ready = scheduled(ready_deployment, 10)
        attempts, executed = [], []

        class Client:
            async def get_scheduled_flow_runs_for_deployments(self, **_):
                return [blocked, ready]

            async def read_flow_run(self, identity):
                return next(run for run in (blocked, ready) if run.id == identity)

            async def set_flow_run_state(self, identity, state, force=False):
                assert state.name == "Pending" and force is False
                attempts.append(identity)
                if identity == blocked.id:
                    return SimpleNamespace(
                        status=blocked_status,
                        state=Scheduled(scheduled_time=now + timedelta(seconds=30)),
                    )
                return SimpleNamespace(status="ACCEPT", state=Pending())

        def execute(**kwargs):
            executed.append(kwargs["flow_run"].id)
            return Completed()

        async def retire(*_, **__):
            raise AssertionError("Neither fresh run is eligible for retirement")

        lane = LightweightCollectorRunner(engine_runner=execute, retire_stale=retire)
        lane._deployments = {
            blocked_deployment: "blocked-flow", ready_deployment: "ready-flow",
        }

        await lane._poll(Client())

        assert executed == [ready.id], "The available collector must run in this polling turn"
        assert attempts == [blocked.id, ready.id]

    asyncio.run(scenario())
