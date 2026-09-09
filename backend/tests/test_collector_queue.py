"""Only untouched, obsolete scheduled collector observations may be retired."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from prefect.client.schemas.objects import StateType
from prefect.client.schemas.responses import OrchestrationResult, SetStateStatus, StateAcceptDetails, StateAbortDetails, StateWaitDetails, StateRejectDetails
from prefect.states import Cancelled, Cancelling, Completed, Pending, Running, Scheduled

from app.workflows import collector_queue as queue

NOW = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)
DEPLOYMENT = uuid4()


def run(**overrides):
    values = dict(id=uuid4(), deployment_id=DEPLOYMENT, auto_scheduled=True,
                  state=Scheduled(scheduled_time=NOW-timedelta(minutes=10)), state_type=StateType.SCHEDULED,
                  start_time=None, run_count=0, infrastructure_pid=None, expected_start_time=NOW-timedelta(minutes=10))
    return SimpleNamespace(**{**values, **overrides})


def result(status, state=None):
    details = StateAcceptDetails() if status == SetStateStatus.ACCEPT else StateWaitDetails(delay_seconds=1, reason='wait') if status == SetStateStatus.WAIT else StateRejectDetails(reason='rewritten') if status == SetStateStatus.REJECT else StateAbortDetails(reason='aborted')
    return OrchestrationResult(status=status, state=state, details=details)


class Client:
    def __init__(self, runs):
        self.runs = {row.id: deepcopy(row) for row in runs}
        self.mutations, self.filters = [], []
        self.cancel_status = SetStateStatus.ACCEPT
        self.lost_ack = False
        self.race_state = None

    async def read_flow_run(self, identity):
        return deepcopy(self.runs[identity])

    async def read_flow_runs(self, **kwargs):
        self.filters.append(kwargs)
        return list(self.runs.values())

    async def set_flow_run_state(self, identity, state, force=False):
        self.mutations.append((identity, deepcopy(state), force))
        row = self.runs[identity]
        if self.race_state:
            row.state = self.race_state
            row.state_type = self.race_state.type
            return result(SetStateStatus.ABORT, row.state)
        if self.cancel_status != SetStateStatus.ACCEPT:
            return result(self.cancel_status, row.state)
        assert state.type == StateType.CANCELLING
        row.state, row.state_type = Cancelled(), StateType.CANCELLED
        if self.lost_ack:
            raise OSError('private-token')
        return result(SetStateStatus.REJECT, row.state)


def test_stale_auto_schedule_rereads_then_cancels_once_without_pending_or_force():
    row = run()
    client = Client([row])
    outcome = asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))
    assert outcome == {'outcome': 'retired'}
    assert [state.type for _, state, _ in client.mutations] == [StateType.CANCELLING]
    assert client.mutations[0][2] is False
    assert str(row.id) not in str(outcome)


@pytest.mark.parametrize('patch', [
    {'auto_scheduled': False}, {'auto_scheduled': None}, {'deployment_id': None},
    {'start_time': NOW-timedelta(seconds=1)}, {'infrastructure_pid': 'existing-process'}, {'run_count': 1}, {'run_count': False},
    {'expected_start_time': NOW}, {'expected_start_time': NOW-timedelta(seconds=120)},
    {'expected_start_time': None}, {'state': Running(), 'state_type': StateType.RUNNING},
    {'state': Pending(), 'state_type': StateType.PENDING}, {'state': Completed(), 'state_type': StateType.COMPLETED},
])
def test_manual_started_future_terminal_or_unscoped_runs_never_mutate(patch):
    row = run(**patch)
    client = Client([row])
    assert asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))['outcome'] == 'skipped'
    assert client.mutations == []


@pytest.mark.parametrize('state', [Running(), Pending(), Completed(), Cancelled()])
def test_fresh_reread_preserves_changed_running_pending_and_terminal_history(state):
    stale = run()
    client = Client([stale])
    client.runs[stale.id].state, client.runs[stale.id].state_type = state, state.type
    assert asyncio.run(queue.retire_stale_scheduled(client, stale, now=NOW))['outcome'] == 'skipped'
    assert client.mutations == []


def test_concurrent_race_server_abort_is_respected_without_force_retry():
    row = run()
    client = Client([row]); client.race_state = Completed()
    assert asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))['outcome'] == 'skipped'
    assert len(client.mutations) == 1
    assert client.runs[row.id].state.type == StateType.COMPLETED


def test_orchestration_wait_preserves_schedule_without_followup_mutations():
    row = run()
    client = Client([row]); client.cancel_status = SetStateStatus.WAIT
    assert asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))['outcome'] == 'deferred'
    assert len(client.mutations) == 1
    assert client.runs[row.id].state.type == StateType.SCHEDULED


def test_lost_cancellation_ack_creates_no_pending_orphan_and_can_be_revisited_safely():
    row = run()
    client = Client([row]); client.lost_ack = True
    outcome = asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))
    assert outcome['outcome'] == 'error' and 'private' not in str(outcome)
    assert client.runs[row.id].state.type == StateType.CANCELLED
    assert asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))['outcome'] == 'skipped'
    assert len(client.mutations) == 1


def test_prune_scopes_deployment_rechecks_auto_flag_and_bounds():
    rows = [run(), run(auto_scheduled=False), run(deployment_id=uuid4()), run(expected_start_time=NOW)]
    client = Client(rows)
    summary = asyncio.run(queue.prune_stale_scheduled(client, [DEPLOYMENT], now=NOW, limit=500))
    assert summary == {'checked': 4, 'retired': 1, 'skipped': 3, 'deferred': 0, 'errors': 0}
    assert len(client.mutations) == 1
    query = client.filters[0]
    assert query['limit'] == 50
    encoded = query['flow_run_filter'].model_dump(mode='json')
    assert encoded['deployment_id']['any_'] == [str(DEPLOYMENT)]
    assert encoded['state']['type']['any_'] == ['SCHEDULED']
    assert encoded['expected_start_time']['before_'].startswith('2026-09-08T05:58:00')
    assert all(str(row.id) not in str(summary) for row in rows)


def test_news_lag_guard_uses_callers_600_second_threshold():
    recent = run(expected_start_time=NOW-timedelta(seconds=599))
    stale = run(expected_start_time=NOW-timedelta(seconds=601))
    client = Client([recent, stale])
    summary = asyncio.run(queue.prune_stale_scheduled(client, [DEPLOYMENT], now=NOW, max_lag_seconds=600))
    assert summary['retired'] == 1 and client.mutations[0][0] == stale.id
    assert client.filters[0]['flow_run_filter'].expected_start_time.before_ == NOW-timedelta(seconds=600)


def test_empty_or_invalid_deployment_scope_never_queries_or_mutates():
    client = Client([run()])
    for ids in [[], [None], ['not-a-uuid']]:
        assert asyncio.run(queue.prune_stale_scheduled(client, ids, now=NOW))['retired'] == 0
    assert client.filters == [] and client.mutations == []


@pytest.mark.parametrize('lag', [-1, 0, float('inf'), float('nan'), 'invalid'])
def test_invalid_lag_configuration_fails_closed(lag):
    row = run(); client = Client([row])
    assert asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW, max_lag_seconds=lag))['outcome'] == 'skipped'
    assert client.mutations == []


def test_errors_from_read_and_filter_are_redacted():
    class Broken(Client):
        async def read_flow_run(self, identity):
            raise OSError('private-key')
        async def read_flow_runs(self, **kwargs):
            raise OSError('private-key')
    row = run(); client = Broken([row])
    outcome = asyncio.run(queue.retire_stale_scheduled(client, row, now=NOW))
    summary = asyncio.run(queue.prune_stale_scheduled(client, [DEPLOYMENT], now=NOW))
    assert outcome['outcome'] == 'error' and summary['errors'] == 1
    assert 'private' not in str(outcome) + str(summary)
    assert client.mutations == []


def test_actual_prefect_policy_aborts_terminal_cancelling_and_rewrites_untouched_schedule():
    # Verify the installed policies used by non-forced orchestration, not an
    # assumed compare-and-swap or a mocked terminal-state policy.
    from prefect.server import models  # noqa: F401 (initialize circular server imports)
    from prefect.server.orchestration.core_policy import HandleFlowTerminalStateTransitions, BypassCancellingFlowRunsWithNoInfra
    from prefect.server.schemas.core import FlowRunPolicy
    async def check():
        terminal_rule = SimpleNamespace(abort_transition=AsyncMock(), reject_transition=AsyncMock())
        context = SimpleNamespace(run=SimpleNamespace(empirical_policy=FlowRunPolicy(), deployment_id=DEPLOYMENT), parameters={})
        await HandleFlowTerminalStateTransitions.before_transition(terminal_rule, Completed(), Cancelling(), context)
        terminal_rule.abort_transition.assert_awaited_once()
        terminal_rule.reject_transition.assert_not_awaited()
        scheduled_rule = SimpleNamespace(reject_transition=AsyncMock())
        await BypassCancellingFlowRunsWithNoInfra.before_transition(scheduled_rule, Scheduled(), Cancelling(),
            SimpleNamespace(run=SimpleNamespace(infrastructure_pid=None)))
        scheduled_rule.reject_transition.assert_awaited_once()
        assert scheduled_rule.reject_transition.await_args.kwargs['state'].type == StateType.CANCELLED
    asyncio.run(check())
