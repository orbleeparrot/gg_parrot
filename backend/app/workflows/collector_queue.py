"""Bounded retirement of obsolete automatic collector observations.

These helpers are only for the fixed public-data collector deployments whose
flows reject equally late observations. They do not cancel trading sessions.
Prefect does not expose an atomic SCHEDULED-only cancellation: the final read
narrows the race, but a concurrent worker may start that already-obsolete run
before Cancelling is proposed. Prefect aborts that proposal for terminal runs
and rewrites untouched schedules without infrastructure directly to Cancelled.
No force, Pending claim, run deletion, or history rewrite is used. Calls must never be applied to arbitrary user deployments.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from uuid import UUID

from prefect.client.schemas.filters import FlowRunFilter
from prefect.client.schemas.objects import StateType
from prefect.client.schemas.responses import SetStateStatus
from prefect.client.schemas.sorting import FlowRunSort
from prefect.states import Cancelling


def _instant(value):
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _cutoff(now, max_lag_seconds):
    now = datetime.now(timezone.utc) if now is None else _instant(now)
    try:
        seconds = float(max_lag_seconds)
        if now is None or not math.isfinite(seconds) or seconds <= 0:
            return None
        return now-timedelta(seconds=seconds)
    except (ValueError, TypeError, OverflowError):
        return None


def _identity(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _eligible(run, cutoff):
    state = getattr(run, 'state', None)
    expected = _instant(getattr(run, 'expected_start_time', None))
    return bool(cutoff is not None and _identity(getattr(run, 'id', None))
        and _identity(getattr(run, 'deployment_id', None))
        and getattr(run, 'auto_scheduled', None) is True
        and getattr(state, 'type', None) == StateType.SCHEDULED
        and getattr(run, 'state_type', None) in {None, StateType.SCHEDULED}
        and getattr(run, 'start_time', None) is None
        and not getattr(run, 'infrastructure_pid', None)
        and type(getattr(run, 'run_count', None)) is int and run.run_count == 0
        and expected is not None and expected < cutoff)


def _error(exc):
    name = type(exc).__name__.lower()
    return 'timeout' if 'timeout' in name else 'network_error' if any(word in name for word in ('network', 'connect')) else 'orchestration_error'


async def retire_stale_scheduled(client, run, *, now=None, max_lag_seconds=120) -> dict:
    """Recheck one caller-scoped stale automatic schedule before cancellation.

    There is deliberately no Pending transition: creating Pending to clean a
    queue can resurrect finished flows or leave an orphan on a network failure.
    The server rewrite to Cancelled is a REJECT response with an actual terminal
    state, which counts as retired. Other ABORT/REJECT/WAIT results are respected.
    Returned metadata contains no run IDs.
    """
    cutoff = _cutoff(now, max_lag_seconds)
    if not _eligible(run, cutoff):
        return {'outcome': 'skipped', 'reason': 'not_obsolete_auto_schedule'}
    try:
        fresh = await client.read_flow_run(run.id)
        if (getattr(fresh, 'id', None) != run.id
                or getattr(fresh, 'deployment_id', None) != run.deployment_id
                or not _eligible(fresh, cutoff)):
            return {'outcome': 'skipped', 'reason': 'state_changed'}
        result = await client.set_flow_run_state(run.id,
            Cancelling(message='Superseded scheduled collector observation'), force=False)
    except Exception as exc:
        # A lost cancellation acknowledgement is safe to revisit next cycle:
        # no intermediate Pending was created and terminal runs are filtered.
        return {'outcome': 'error', 'error_code': _error(exc)}
    status = getattr(result, 'status', None)
    actual = getattr(getattr(result, 'state', None), 'type', None)
    if status in {SetStateStatus.ACCEPT, SetStateStatus.REJECT} and actual == StateType.CANCELLED:
        return {'outcome': 'retired'}
    if status == SetStateStatus.WAIT or (status == SetStateStatus.ACCEPT and actual == StateType.CANCELLING):
        return {'outcome': 'deferred', 'reason': 'orchestration_wait'}
    return {'outcome': 'skipped', 'reason': 'orchestration_declined'}


async def prune_stale_scheduled(client, deployment_ids, *, now=None, limit=50, max_lag_seconds=120) -> dict:
    """Bounded, scoped queue maintenance; no deletion or manual/run-state sweep.

    Prefect 3.7.8 does not expose auto_scheduled in FlowRunFilter. We restrict
    deployment/state/time server-side and enforce auto_scheduled locally before
    each write. Manual entries returned in that bounded page stay untouched.
    """
    summary = {'checked': 0, 'retired': 0, 'skipped': 0, 'deferred': 0, 'errors': 0}
    cutoff = _cutoff(now, max_lag_seconds)
    scope = {_identity(value) for value in (deployment_ids or [])}
    if not scope or None in scope or cutoff is None:
        return summary
    try:
        bound = max(1, min(50, int(limit)))
    except (ValueError, TypeError, OverflowError):
        return {**summary, 'errors': 1, 'error_code': 'invalid_limit'}
    try:
        rows = await client.read_flow_runs(
            flow_run_filter=FlowRunFilter(
                deployment_id={'any_': sorted(scope, key=str)},
                state={'type': {'any_': [StateType.SCHEDULED]}},
                expected_start_time={'before_': cutoff}),
            sort=FlowRunSort.EXPECTED_START_TIME_ASC, limit=bound)
    except Exception as exc:
        return {**summary, 'errors': 1, 'error_code': _error(exc)}
    for run in rows[:bound]:
        summary['checked'] += 1
        if _identity(getattr(run, 'deployment_id', None)) not in scope:
            summary['skipped'] += 1
            continue
        outcome = await retire_stale_scheduled(client, run, now=now, max_lag_seconds=max_lag_seconds)
        key = 'errors' if outcome['outcome'] == 'error' else outcome['outcome']
        summary[key] += 1
    return summary
