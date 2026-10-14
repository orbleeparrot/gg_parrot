"""One actual event, two projections; no orders, AI or production database."""
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlmodel import select

from app import agent_logs, notifications, runner
from app.db import NotificationMessage, RunSession, RunSessionEvent, get_session


@pytest.fixture
def session_row():
    with get_session() as db:
        row = RunSession(user_id=1, symbol="LINKUSDT", runner_version="17", status="running",
                         mode="mock", started_at=runner._now_iso(), last_heartbeat_at=runner._now_iso())
        db.add(row)
        db.commit()
        sid = row.id
    yield sid
    with get_session() as db:
        for model in (NotificationMessage, RunSessionEvent):
            for item in db.exec(select(model).where(model.session_id == sid)).all():
                db.delete(item)
        db.delete(db.get(RunSession, sid))
        db.commit()


def test_runner_alert_references_the_identical_persisted_event(session_row):
    event = {"kind": "fill", "message": "실행기 보고 · 모의 매수", "event_key": "runner:one-real-origin",
             "event_name": "runner.message", "ts": runner._now_iso()}
    with get_session() as db:
        row = db.get(RunSession, session_row)
        runner._append_events(db, row, [event])
        db.commit()
        stored = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == row.id)).one()
        alert = db.exec(select(NotificationMessage).where(NotificationMessage.session_id == row.id)).one()
        data = json.loads(alert.data_json)
        assert data["activity_event_id"] == stored.id
        assert data["activity_event_key"] == stored.event_key
        assert alert.link == f"/agents?session={row.id}&panel=logs&event={stored.id}"
        assert alert.body == stored.message


def test_direct_server_agent_notification_also_has_one_canonical_record(session_row):
    with get_session() as db:
        first = notifications.notify(db, 1, "agent", "실제 서버 관측", "발생한 상태 변경", session_id=session_row,
                                     ref="one-observation")
        db.commit()
        stored = db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == session_row)).one()
        assert json.loads(first.data_json)["activity_event_id"] == stored.id
        assert stored.message == first.body
        assert notifications.notify(db, 1, "agent", "실제 서버 관측", "발생한 상태 변경", session_id=session_row,
                                    ref="one-observation") is None
        db.commit()
        assert len(db.exec(select(RunSessionEvent).where(RunSessionEvent.session_id == session_row)).all()) == 1


def test_saved_event_keys_are_acknowledged_only_when_the_sink_succeeded(session_row, monkeypatch):
    event = {"kind": "info", "message": "실제 실행기 메시지", "event_key": "runner:pending-one"}
    user = SimpleNamespace(id=1)
    first = runner.record_runner_events(user, session_row, [event])
    assert first["accepted_event_keys"] == [event["event_key"]]
    assert runner.record_runner_events(user, session_row, [event])["accepted_event_keys"] == [event["event_key"]]
    monkeypatch.setattr(runner, "_append_event_batch", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("sink down")))
    event = {**event, "event_key": "runner:not-yet-saved"}
    assert runner.record_runner_events(user, session_row, [event])["accepted_event_keys"] == []


def test_events_only_recovery_does_not_change_trade_state_or_reopen_ended_run(session_row):
    with get_session() as db:
        row = db.get(RunSession, session_row)
        row.status, row.in_position, row.position_qty, row.entry_price = "stopped", True, 2, 100
        db.add(row)
        db.commit()
    result = runner.record_runner_events(SimpleNamespace(id=1), session_row,
                                        [{"kind": "info", "message": "종료 전 미전송 기록", "event_key": "runner:old-run"}])
    assert result["accepted_event_keys"] == ["runner:old-run"]
    with get_session() as db:
        row = db.get(RunSession, session_row)
        assert (row.status, row.in_position, row.position_qty, row.entry_price) == ("stopped", True, 2, 100)
    with pytest.raises(Exception) as error:
        runner.record_runner_events(SimpleNamespace(id=2), session_row, [])
    assert error.value.status_code == 404


def test_browser_observation_is_idempotent_and_cannot_impersonate_an_order(session_row):
    payload = {"client_event_id": str(uuid4()), "event_name": "browser.connection_observed",
               "observed_at": runner._now_iso(), "details": {"module": "position_news", "state": "transport_error",
                   "title": "뉴스 서버 연결 지연", "summary": "브라우저 조회가 실패했어요.",
                   "verification": "exchange_verified", "command_id": 42}}
    with get_session() as db:
        first = agent_logs.record_observation(1, session_row, payload, db=db)
        second = agent_logs.record_observation(1, session_row, payload, db=db)
        assert first["event_id"] == second["event_id"]
        assert first["recorded"] is True and second["recorded"] is False
        event = first["event"]
        assert event["id"] == first["event_id"] and event["session_id"] == session_row
        assert event["event_key"] == f"browser:{payload['client_event_id']}"
        assert event["component"] == "browser_observation"
        assert event["details"]["verification"] == "browser_observation_not_exchange_event"
        assert "command_id" not in event["details"] and not event["operation_id"]
        assert event["message"] == payload["details"]["title"] + " · " + payload["details"]["summary"]
        with pytest.raises(Exception) as error:
            agent_logs.record_observation(1, session_row, {**payload, "event_name": "runner.order_result"}, db=db)
        assert error.value.status_code == 422


def test_browser_observation_owner_and_actual_event_time_are_retained(session_row):
    payload = {"client_event_id": str(uuid4()), "event_name": "browser.risk_observed",
               "observed_at": "2026-10-14T04:00:00Z", "details": {"title": "평가손실 관측", "metric_value": -3.2}}
    with get_session() as db:
        with pytest.raises(Exception) as error:
            agent_logs.record_observation(2, session_row, payload, db=db)
        assert error.value.status_code == 404
        result = agent_logs.record_observation(1, session_row, payload, db=db)
        assert result["event"]["occurred_at"].startswith("2026-10-14T04:00:00")
        assert result["event"]["details"]["metric_value"] == -3.2
