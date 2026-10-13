"""Structured telemetry is immutable, owner/session scoped, and never executes orders."""
import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app import runner, runner_engine
from app.db import RunSession, RunSessionEvent, RunnerCommand, get_session


@pytest.fixture
def sessions():
    ids = []
    with get_session() as db:
        for symbol in ("ONEUSDT", "BTCUSDT"):
            row = RunSession(user_id=1, symbol=symbol, runner_version="10", status="running",
                             started_at=runner._now_iso(), last_heartbeat_at=runner._now_iso())
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    yield ids
    with get_session() as db:
        for model in (RunSessionEvent, RunnerCommand):
            for row in db.exec(select(model).where(model.session_id.in_(ids))).all():
                db.delete(row)
        for sid in ids:
            db.delete(db.get(RunSession, sid))
        db.commit()


def _events(db, sid):
    return db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == sid)
                   .order_by(RunSessionEvent.id)).all()


def _command(db, sid, action="buy"):
    row = db.get(RunSession, sid)
    return runner_engine.insert_command(db, row, {
        "action": action, "notional_frac": .5 if action == "buy" else 0,
        "qty_frac": 0 if action == "buy" else 1, "signal_price": 12.5,
        "reason": "RSI 23 ≤ 25 · 진입" if action == "buy" else "청산",
    }, now_ms=1_000)


def test_stable_key_deduplicates_retried_batch_and_notifications(sessions, monkeypatch):
    notifications = []
    monkeypatch.setattr(runner.notifications_mod, "notify", lambda *args, **kwargs: notifications.append(args))
    monkeypatch.setattr(runner.notifications_mod, "trim_user", lambda *args: None)
    event = {"ts": "2026-10-13T03:00:00Z", "kind": "order", "message": "실행기 주문 결과 보고",
             "event_key": "runner:test-order", "severity": "INFO", "event_name": "runner.message",
             "details": {"executed_qty": 2, "fill_price": 12.6}}
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        assert runner._append_events(db, row, [event, dict(event)]) == 1
        db.commit()
        assert runner._append_events(db, row, [dict(event)]) == 0
        db.commit()
        events = _events(db, row.id)
        assert len(events) == 1 and events[0].severity == "INFO"
        assert events[0].component == "runner" and events[0].event_key == event["event_key"]
        assert len(notifications) == 1


def test_same_event_key_is_independent_between_sessions(sessions):
    event = {"kind": "info", "message": "연결됨", "event_key": "runner:same"}
    with get_session() as db:
        for sid in sessions:
            assert runner._append_events(db, db.get(RunSession, sid), [event]) == 1
        db.commit()
        assert all(len(_events(db, sid)) == 1 for sid in sessions)


def test_extra_structured_stages_do_not_duplicate_original_notifications(sessions, monkeypatch):
    notifications = []
    monkeypatch.setattr(runner.notifications_mod, "notify", lambda *args, **kwargs: notifications.append(args))
    monkeypatch.setattr(runner.notifications_mod, "trim_user", lambda *args: None)
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        runner._append_events(db, row, [
            {"kind": "fill", "message": "실행기 상세 보고", "event_name": "runner.order_result"},
            {"kind": "fill", "message": "기존 실행기 체결 메시지"},
        ])
        db.commit()
        assert len(_events(db, row.id)) == 2 and len(notifications) == 1


def test_legacy_events_without_key_are_not_coalesced_or_given_inferred_severity(sessions):
    event = {"kind": "error", "message": "기존 오류 메시지"}
    with get_session() as db:
        assert runner._append_events(db, db.get(RunSession, sessions[0]), [event, dict(event)]) == 2
        db.commit()
        events = _events(db, sessions[0])
        assert all(e.event_key is None and e.severity == "UNKNOWN" for e in events)
        assert all(e.ts == "" and e.created_ms > 0 for e in events)


def test_uploaded_provenance_cannot_claim_server_or_verified_exchange_result(sessions):
    with get_session() as db:
        foreign = _command(db, sessions[1])
        event = {"kind": "fill", "message": "체결 확인 주장", "component": "engine",
                 "event_key": "news:forged", "event_name": "command.execution_reported",
                 "operation_id": f"command:{foreign.id}",
                 "details": {"verification": "exchange_verified", "ack_source": "exchange"}}
        runner._append_events(db, db.get(RunSession, sessions[0]), [event])
        db.commit()
        saved = _events(db, sessions[0])[0]
        assert saved.component == "runner" and saved.event_name == "runner.message"
        assert saved.operation_id == "" and saved.event_key.startswith("runner:")
        details = json.loads(saved.details_json)
        assert details["verification"] == "runner_reported_not_exchange_verified"
        assert details["ack_source"] == "runner"


def test_runner_can_link_only_its_session_command(sessions):
    with get_session() as db:
        cmd = _command(db, sessions[0])
        runner._append_events(db, db.get(RunSession, sessions[0]), [{
            "kind": "info", "message": "명령 수신", "event_name": "runner.command_received",
            "operation_id": f"command:{cmd.id}",
        }])
        db.commit()
        assert _events(db, sessions[0])[0].operation_id == f"command:{cmd.id}"


def test_local_order_request_result_can_correlate_inside_own_session(sessions):
    with get_session() as db:
        operation = "order:ggp-" + "a" * 28
        runner._append_events(db, db.get(RunSession, sessions[0]), [
            {"kind": "order", "message": "요청", "operation_id": operation,
             "event_name": "runner.order_requested", "details": {"requested_qty": 2}},
            {"kind": "fill", "message": "결과", "operation_id": operation,
             "event_name": "runner.order_result", "details": {"executed_qty": 2}},
        ])
        db.commit()
        assert [e.operation_id for e in _events(db, sessions[0])] == [operation, operation]


def test_logging_failure_keeps_command_ack_transaction(sessions, monkeypatch):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        cmd = _command(db, row.id)
        db.commit()
        monkeypatch.setattr(runner.notifications_mod, "notify", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("log sink unavailable")))
        runner_engine.apply_acks(db, row, [{"command_id": cmd.id, "ok": True,
                                         "executed_qty": 2, "fill_price": 12.6}], now_ms=2_000)
        db.commit()
        db.refresh(cmd)
        assert cmd.status == "acked" and cmd.attempts == 1
        assert _events(db, row.id) == []
        # A retry of the same acknowledgement must never produce a second execution or new attempt.
        runner_engine.apply_acks(db, row, [{"command_id": cmd.id, "ok": True}], now_ms=3_000)
        db.commit()
        assert cmd.attempts == 1


def test_created_and_execution_report_events_keep_immutable_snapshots(sessions):
    live = SimpleNamespace(session_id=sessions[0], driver=SimpleNamespace(initial=32), stop_flag=False)
    fill = SimpleNamespace(side="buy", qty=1.28, price=12.5, reason="RSI 23 ≤ 25 · 진입", qty_before=0)
    runner_engine._persist_fill(live, fill, "{}")
    runner_engine._persist_fill(live, fill, "{}")
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        cmd = db.exec(select(RunnerCommand).where(RunnerCommand.session_id == row.id)).one()
        created = _events(db, row.id)[0]
        assert created.event_name == "command.created" and created.component == "engine"
        assert created.operation_id == f"command:{cmd.id}"
        before = created.details_json
        assert json.loads(before)["status"] == "pending" and json.loads(before)["attempts"] == 0
        assert "executed_qty" not in json.loads(before) and "fill_price" not in json.loads(before)
        runner_engine.apply_acks(db, row, [{"command_id": cmd.id, "ok": True,
                                         "executed_qty": 1.28, "fill_price": 12.6}], now_ms=2_000)
        db.commit()
        db.refresh(created)
        assert created.details_json == before
        reported = _events(db, row.id)[1]
        assert reported.event_name == "command.execution_reported" and reported.operation_id == created.operation_id
        details = json.loads(reported.details_json)
        assert details["executed_qty"] == 1.28 and details["fill_price"] == 12.6
        assert details["verification"] == "runner_reported_not_exchange_verified"
        assert "order_id" not in details and "fees" not in details


def test_failure_and_retry_have_separate_stages_and_frozen_attempts(sessions):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        cmd = _command(db, row.id, "sell")
        db.commit()
        for attempt in (1, 2, 3):
            runner_engine.apply_acks(db, row, [{"command_id": cmd.id, "ok": False,
                                             "error": "insufficient"}], now_ms=2_000 + attempt)
            db.commit()
        events = _events(db, row.id)
        assert [e.event_name for e in events] == ["command.failed", "command.retry_scheduled",
                                                "command.failed", "command.retry_scheduled", "command.failed"]
        assert [json.loads(e.details_json)["attempts"] for e in events] == [1, 1, 2, 2, 3]
        assert all(e.operation_id == f"command:{cmd.id}" for e in events)


def test_expired_command_is_logged_once_not_each_poll(sessions):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        cmd = _command(db, row.id)
        db.commit()
        runner_engine.pending_commands(db, row, now_ms=500_000)
        runner_engine.pending_commands(db, row, now_ms=500_001)
        db.commit()
        events = _events(db, row.id)
        assert len(events) == 1 and events[0].event_name == "command.expired"
        assert json.loads(events[0].details_json)["status"] == "expired"
