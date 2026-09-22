"""에이전트 손익은 '투입금 대비 총수익률'로 — 투입금 = 세션 중 실제로 들어간 최대 금액(수량×진입가)."""
from app import runner
from app.db import RunSession, User, get_session, init_db


def _session(**over):
    init_db()
    user = User(id=913001, email="ret@example.invalid", username="ret", password_hash="unused")
    fields = dict(user_id=user.id, symbol="ONEUSDT", status="running", started_at="2026-09-22T00:00:00Z",
                  last_heartbeat_at="2026-09-22T00:00:00Z")
    fields.update(over)
    with get_session() as db:
        row = RunSession(**fields)
        db.add(row)
        db.commit()
        db.refresh(row)
        return user, row.id


def test_heartbeat_tracks_max_invested_and_returns_total_return():
    user, sid = _session()
    runner.heartbeat(user, sid, {"in_position": True, "position_qty": 2000, "entry_price": 0.01, "last_price": 0.0105,
                                 "unrealized_pct": 5.0, "realized_pnl": 0.0})
    view = runner.get_owned_session(user.id, sid)
    assert abs(view["invested_usdt"] - 20.0) < 1e-9
    assert abs(view["return_pct"] - 5.0) < 1e-9          # (0 실현 + 1.0 평가) / 20 투입
    # 더 큰 포지션이 들어오면 투입금은 최대값으로 올라가고, 줄어도 내려가지 않는다
    runner.heartbeat(user, sid, {"in_position": True, "position_qty": 4000, "entry_price": 0.01, "last_price": 0.01,
                                 "unrealized_pct": 0.0, "realized_pnl": 0.0})
    runner.heartbeat(user, sid, {"in_position": False, "position_qty": 0, "entry_price": 0, "last_price": 0.0102,
                                 "unrealized_pct": 0.0, "realized_pnl": 0.8})
    view = runner.get_owned_session(user.id, sid)
    assert abs(view["invested_usdt"] - 40.0) < 1e-9
    assert abs(view["return_pct"] - 2.0) < 1e-9          # 0.8 / 40


def test_return_is_none_before_anything_was_invested():
    user, sid = _session()
    view = runner.get_owned_session(user.id, sid)
    assert view["invested_usdt"] == 0.0 and view["return_pct"] is None


def test_stopped_session_reports_realized_over_invested():
    user, sid = _session(invested_usdt=50.0, realized_pnl=-2.5, status="stopped", in_position=False)
    view = runner.get_owned_session(user.id, sid)
    assert abs(view["return_pct"] + 5.0) < 1e-9
