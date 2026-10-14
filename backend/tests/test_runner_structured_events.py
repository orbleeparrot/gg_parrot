"""Structured telemetry is immutable, owner/session scoped, and never executes orders."""
import json
from types import SimpleNamespace

import pytest
from sqlmodel import select

from app import runner, runner_engine
from app.db import NotificationMessage, RunSession, RunSessionEvent, RunnerCommand, get_session


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
        for model in (NotificationMessage, RunSessionEvent, RunnerCommand):
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
            {"kind": "order", "message": "주문 요청", "event_name": "runner.order_requested"},
            {"kind": "fill", "message": "실제 실행기 체결 결과", "event_name": "runner.order_result"},
            {"kind": "info", "message": "명령 처리 보고", "event_name": "runner.command_reported"},
        ])
        db.commit()
        assert len(_events(db, row.id)) == 3
        assert len(notifications) == 1
        assert notifications[0][4] == "실제 실행기 체결 결과"


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


@pytest.mark.parametrize("mode", ["mock", "testnet", "live"])
def test_uploaded_order_mode_cannot_override_known_session_mode(sessions, mode):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        row.mode = mode
        db.commit()
        runner._append_events(db, row, [{"kind": "fill", "message": "주문 결과 보고",
                                       "event_name": "runner.order_result",
                                       "details": {"mode": "live" if mode != "live" else "mock"}}])
        db.commit()
        details = json.loads(_events(db, row.id)[0].details_json)
        assert details["mode"] == mode
        assert details["verification"] == "runner_reported_not_exchange_verified"


@pytest.mark.parametrize("incoming", ["mock", "testnet", "live", "", "invented_mode"])
def test_unknown_session_mode_keeps_only_explicit_canonical_runner_claim(sessions, incoming):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        assert row.mode == ""
        runner._append_events(db, row, [{"kind": "info", "message": "실행기 보고",
                                       "details": {"mode": incoming}}])
        db.commit()
        details = json.loads(_events(db, row.id)[0].details_json)
        if incoming in {"mock", "testnet", "live"}:
            assert details["mode"] == incoming
        else:
            assert "mode" not in details
        assert details["verification"] == "runner_reported_not_exchange_verified"


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


@pytest.mark.parametrize("outcome, phrase", [
    ("no_position_no_order", "주문 없음"),
    ("duplicate_command_no_reexecution", "재실행 없음"),
    (None, "명령 처리 확인"),
])
def test_successful_no_order_ack_is_not_logged_as_a_zero_fill(sessions, outcome, phrase):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        row.mode = "live"
        cmd = _command(db, row.id, "sell")
        db.commit()
        ack = {"command_id": cmd.id, "ok": True}
        if outcome:
            ack["outcome"] = outcome
        runner_engine.apply_acks(db, row, [ack], now_ms=2_000)
        db.commit()
        reported = _events(db, row.id)[0]
        details = json.loads(reported.details_json)
        assert cmd.status == "acked" and cmd.attempts == 1
        assert phrase in reported.message
        assert "체결 보고" not in reported.message and "0 @ 0" not in reported.message
        assert reported.kind == "info"
        assert details["outcome"] == (outcome or "acknowledged_execution_unknown")
        assert details["mode"] == "live"
        assert "executed_qty" not in details and "fill_price" not in details
        runner_engine.apply_acks(db, row, [ack], now_ms=3_000)
        db.commit()
        assert len(_events(db, row.id)) == 1 and cmd.attempts == 1


@pytest.mark.parametrize("mode, phrase", [
    ("mock", "모의 체결 보고"), ("testnet", "테스트넷 체결 보고"),
    ("live", "실전 체결 수량·가격 보고"), ("", "모드 미확인"),
])
def test_actual_quantity_ack_uses_persisted_mode_not_client_claim(sessions, mode, phrase):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        row.mode = mode
        cmd = _command(db, row.id)
        db.commit()
        runner_engine.apply_acks(db, row, [{"command_id": cmd.id, "ok": True,
                                         "executed_qty": 2, "fill_price": 12.6,
                                         "mode": "live" if mode != "live" else "mock"}], now_ms=2_000)
        db.commit()
        reported = _events(db, row.id)[0]
        details = json.loads(reported.details_json)
        assert phrase in reported.message
        assert details["mode"] == mode and details["outcome"] == "execution_reported"
        assert details["verification"] == "runner_reported_not_exchange_verified"
        assert "fees" not in details and "exchange_order_id" not in details


@pytest.mark.parametrize("qty, price", [(2, 0), (0, 12.6), (-2, 12.6)])
def test_legacy_ack_without_both_positive_fill_facts_never_claims_completed_fill(sessions, qty, price):
    with get_session() as db:
        row = db.get(RunSession, sessions[0])
        cmd = _command(db, row.id)
        db.commit()
        runner_engine.apply_acks(db, row, [{"command_id": cmd.id, "ok": True,
                                         "executed_qty": qty, "fill_price": price}], now_ms=2_000)
        db.commit()
        reported = _events(db, row.id)[0]
        assert "명령 처리 확인" in reported.message and "체결 보고" not in reported.message
        details = json.loads(reported.details_json)
        assert details["outcome"] == "acknowledged_execution_unknown"
        # Keep an explicitly reported positive fact, but never synthesize its
        # missing counterpart or describe the incomplete pair as a fill.
        for field, value in (("executed_qty", qty), ("fill_price", price)):
            if value > 0:
                assert details[field] == value
            else:
                assert field not in details
