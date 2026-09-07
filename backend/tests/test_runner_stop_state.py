from app import runner
from app.db import RunSession, User, get_session, init_db


def _session():
    init_db()
    user = User(id=912345, email="stop-state@example.invalid", username="stop-state", password_hash="unused")
    with get_session() as db:
        row = RunSession(user_id=user.id, symbol="BTCUSDT", in_position=True,
                         position_qty=2, entry_price=100, last_price=105,
                         status="running", stop_mode="stop_only", started_at="2026-09-07T00:00:00Z")
        db.add(row)
        db.commit()
        db.refresh(row)
        return user, row.id


def test_legacy_stop_preserves_open_position():
    user, session_id = _session()
    runner.mark_stopped(user, session_id, note="매크로만 종료 — 포지션 유지")
    with get_session() as db:
        row = db.get(RunSession, session_id)
        assert row.in_position is True
        assert row.position_qty == 2


def test_failed_close_snapshot_remains_visible_as_error():
    user, session_id = _session()
    runner.mark_stopped(user, session_id, "error", "청산 실패 — 포지션 남음", snapshot={
        "in_position": True, "position_qty": 2, "entry_price": 100, "last_price": 105,
    })
    with get_session() as db:
        row = db.get(RunSession, session_id)
        assert row.status == "error"
        assert row.in_position is True


def test_confirmed_close_persists_final_position_and_pnl():
    user, session_id = _session()
    runner.mark_stopped(user, session_id, snapshot={
        "in_position": False, "position_qty": 0, "entry_price": 0,
        "last_price": 105, "realized_pnl": 10, "unrealized_pct": 0,
    })
    with get_session() as db:
        row = db.get(RunSession, session_id)
        assert row.in_position is False
        assert row.position_qty == 0
        assert row.realized_pnl == 10


def test_uncertain_order_is_preserved_and_never_reported_as_confirmed_close():
    user, session_id = _session()
    runner.heartbeat(user, session_id, {
        "in_position": True, "position_uncertain": True, "position_qty": 0,
    })
    runner.mark_stopped(user, session_id, snapshot={
        "in_position": False, "position_uncertain": True, "position_qty": 0,
    })
    view = runner.get_owned_session(user.id, session_id)
    assert view["position_uncertain"] is True
    assert view["status"] == "error"
    assert "확인" in view["note"]
