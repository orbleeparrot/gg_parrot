"""Run bounded public-data flows in one thread beside the news subprocess.

Prefect still owns each deployment, scheduled run, state transition and task log.
Only the two checked-in HTTP collectors are accepted here; browser/news flows
retain the official subprocess runner. No user trading process runs in this lane.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
from uuid import UUID

from prefect.client.orchestration import get_client
from prefect.client.schemas.filters import (
    DeploymentFilter, DeploymentFilterId, FlowRunFilter,
    FlowRunFilterNextScheduledStartTime, FlowRunFilterState, FlowRunFilterStateType,
)
from prefect.client.schemas.objects import StateType
from prefect.client.schemas.sorting import FlowRunSort
from prefect.flow_engine import run_flow
from prefect.states import Cancelled, Failed, Pending

MAX_LAG_SECONDS = 120
POLL_LIMIT = 200
_ENTRYPOINTS = {
    "app.workflows.whale_activity.collect_whale_activity_flow",
    "app.workflows.onchain_holders.collect_onchain_holders_flow",
}


def _known_flow(entrypoint):
    if entrypoint == "app.workflows.whale_activity.collect_whale_activity_flow":
        from .whale_activity import collect_whale_activity_flow
        return collect_whale_activity_flow
    if entrypoint == "app.workflows.onchain_holders.collect_onchain_holders_flow":
        from .onchain_holders import collect_onchain_holders_flow
        return collect_onchain_holders_flow
    raise ValueError("Unsupported lightweight collector entrypoint")


def _utc(value):
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _scheduled_at(run):
    # expected_start_time preserves original age after a concurrency requeue.
    return _utc(getattr(run, "expected_start_time", None)) or _utc(getattr(run, "next_scheduled_start_time", None))


def _is_scheduled(run):
    return bool(getattr(run, "state", None) and run.state.is_scheduled())


def _accepted_pending(result):
    return (str(getattr(result, "status", "")) in {"ACCEPT", "SetStateStatus.ACCEPT"}
            and getattr(result, "state", None) is not None and result.state.is_pending())


def _event(name, **values):
    # No upstream text, account identifiers, credentials or exception reprs.
    print(json.dumps({"event": name, **values}), flush=True)


class LightweightCollectorRunner:
    """Minimal supervisor lane with one in-flight Prefect engine thread."""

    def __init__(self, *, name="gg-parrot-whales", limit=1, query_seconds=5,
                 pause_on_shutdown=False, client_factory=None, engine_runner=None,
                 retire_stale=None, flow_resolver=None):
        if limit != 1 or pause_on_shutdown:
            raise ValueError("Lightweight collectors require one slot and persistent schedules")
        if not isinstance(query_seconds, (int, float)) or not math.isfinite(query_seconds) or query_seconds <= 0:
            raise ValueError("Invalid lightweight collector polling interval")
        self.name = name
        self.query_seconds = max(1, float(query_seconds))
        self.started = False
        self._client_factory = client_factory or get_client
        self._engine_runner = engine_runner or run_flow
        self._flow_resolver = flow_resolver or _known_flow
        self._retire_stale = retire_stale
        self._deployments = {}
        self._last_served = {}
        self._stopping = asyncio.Event()
        self._finished = asyncio.Event()
        self._inflight = None

    async def aadd_deployment(self, deployment):
        if self.started:
            raise RuntimeError("Register lightweight deployments before starting")
        if deployment.entrypoint not in _ENTRYPOINTS:
            raise ValueError("Unsupported lightweight collector entrypoint")
        flow = self._flow_resolver(deployment.entrypoint)
        if getattr(deployment, "version", None) and hasattr(flow, "with_options"):
            flow = flow.with_options(version=deployment.version)
        if hasattr(deployment, "aapply"):
            deployment_id = await deployment.aapply()
        else:
            deployment_id = await asyncio.to_thread(deployment.apply)
        deployment_id = UUID(str(deployment_id))
        self._deployments[deployment_id] = flow
        return deployment_id

    async def _fresh_runs(self, client, runs, now):
        if len(runs) >= POLL_LIMIT:
            # The normal endpoint returns oldest first. A long outage can fill
            # its entire page with obsolete work, so query the fresh window too.
            fresh = await client.read_flow_runs(
                deployment_filter=DeploymentFilter(id=DeploymentFilterId(any_=list(self._deployments))),
                flow_run_filter=FlowRunFilter(
                    state=FlowRunFilterState(type=FlowRunFilterStateType(any_=[StateType.SCHEDULED])),
                    next_scheduled_start_time=FlowRunFilterNextScheduledStartTime(
                        after_=now-timedelta(seconds=MAX_LAG_SECONDS), before_=now)),
                sort=FlowRunSort.NEXT_SCHEDULED_START_TIME_ASC, limit=20,
            )
            runs = [*runs, *fresh]
        unique = {}
        for run in runs:
            scheduled = _scheduled_at(run)
            due = _utc(getattr(run, "next_scheduled_start_time", None)) or scheduled
            if (run.deployment_id in self._deployments and _is_scheduled(run) and scheduled and due
                    and now-timedelta(seconds=MAX_LAG_SECONDS) <= scheduled <= now and due <= now):
                unique[run.id] = run
        return sorted(unique.values(), key=lambda run: (
            self._last_served.get(run.deployment_id, datetime.min.replace(tzinfo=timezone.utc)),
            _scheduled_at(run), str(run.id)))

    async def _retire(self, client, runs, now):
        if self._retire_stale is None:
            from .collector_queue import retire_stale_scheduled
            retire = retire_stale_scheduled
        else:
            retire = self._retire_stale
        stale = [run for run in runs if run.deployment_id in self._deployments
                 and _is_scheduled(run) and _scheduled_at(run)
                 and _scheduled_at(run) < now-timedelta(seconds=MAX_LAG_SECONDS)]
        for run in stale[:3]:
            if self._stopping.is_set():
                break
            await retire(client, run, now=now, max_lag_seconds=MAX_LAG_SECONDS)

    async def _execute(self, client, run):
        if self._stopping.is_set():
            return False
        current = await client.read_flow_run(run.id)
        now = datetime.now(timezone.utc)
        scheduled = _scheduled_at(current)
        if (current.deployment_id != run.deployment_id or not _is_scheduled(current)
                or current.start_time is not None or current.run_count
                or not scheduled or not now-timedelta(seconds=MAX_LAG_SECONDS) <= scheduled <= now):
            return False
        run = current
        # Default Pending atomically rejects a competing runner's Pending or
        # Running state. Do not force or rename this state: both bypass guards.
        result = await client.set_flow_run_state(run.id, Pending(), force=False)
        if not _accepted_pending(result):
            _event("lightweight_collector_claim_deferred")
            return False
        claimed = run.model_copy(update={"state": result.state, "state_type": result.state.type,
                                         "state_name": result.state.name, "state_id": result.state.id})
        self._last_served[run.deployment_id] = datetime.now(timezone.utc)
        self._inflight = asyncio.create_task(asyncio.to_thread(
            self._engine_runner, flow=self._deployments[run.deployment_id],
            flow_run=claimed, return_type="state"))
        try:
            state = await asyncio.shield(self._inflight)
            _event("lightweight_collector_completed", state=str(getattr(state, "type", "unknown")))
        except asyncio.CancelledError:
            # Cancelling an asyncio await cannot stop a Python worker thread.
            # Keep its bounded engine alive until it releases Prefect/DB leases.
            self._stopping.set()
            await asyncio.shield(self._inflight)
            raise
        except BaseException as exc:
            from prefect.exceptions import Abort, Pause
            if isinstance(exc, (Abort, Pause)):
                _event("lightweight_collector_engine_stopped")
            elif isinstance(exc, Exception):
                # This engine thread has exited. Only its own nonterminal run
                # may be closed; never update another runner's pending records.
                current = await client.read_flow_run(run.id)
                if current.state and (current.state.is_pending() or current.state.is_running()):
                    await client.set_flow_run_state(run.id,
                        Failed(message="Lightweight collector engine stopped unexpectedly."), force=False)
                _event("lightweight_collector_engine_error", error_code="engine_error")
            else:
                raise
        finally:
            self._inflight = None
            # Inline runs have no child process for Prefect's cancellation
            # observer to reap. The owning engine thread has now exited.
            current = await client.read_flow_run(run.id)
            if current.state and current.state.is_cancelling():
                await client.set_flow_run_state(run.id,
                    Cancelled(message="Bounded lightweight collector stopped."), force=False)
        return True

    async def _poll(self, client):
        now = datetime.now(timezone.utc)
        # This public endpoint also sends the same deployment READY heartbeat
        # used by Prefect's official Runner.
        runs = await client.get_scheduled_flow_runs_for_deployments(
            deployment_ids=list(self._deployments), scheduled_before=now, limit=POLL_LIMIT)
        fresh = await self._fresh_runs(client, runs, now)
        attempted = set()
        for run in fresh:
            if run.deployment_id in attempted:
                continue
            attempted.add(run.deployment_id)
            if await self._execute(client, run):
                break
        await self._retire(client, runs, now)

    async def start(self):
        if self.started or not self._deployments:
            raise RuntimeError("Lightweight runner requires registered deployments and a single start")
        self.started = True
        self._finished.clear()
        failures = 0
        try:
            async with self._client_factory() as client:
                while not self._stopping.is_set():
                    try:
                        await self._poll(client)
                        failures = 0
                    except Exception:
                        failures += 1
                        _event("lightweight_collector_poll_error", error_code="prefect_unavailable")
                    delay = min(60, self.query_seconds * (2**min(failures, 4)))
                    try:
                        await asyncio.wait_for(self._stopping.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
        finally:
            self.started = False
            self._finished.set()

    async def astop(self):
        self._stopping.set()
        if self.started:
            await self._finished.wait()
