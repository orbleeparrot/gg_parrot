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


# --- 수수료 반영 손익(실행기 0.20.13+) — 실행기 화면과 웹이 같은 숫자 -------------------------
# 2026-10-18: KRW-SAND 실행기 '평가손익' +1.23% · 웹 +1.33%. 실행기는 사고팔 때 수수료(업비트 0.05% × 2)를 뺀
# 값을 보이는데, 서버는 수수료 전(평단 대비 가격)만 받아 같은 이름으로 다른 숫자를 냈다.

def _beat(user, sid, **net):
    runner.heartbeat(user, sid, {"in_position": True, "position_qty": 1000, "entry_price": 83.0, "last_price": 83.9,
                                 "unrealized_pct": 1.0843, "realized_pnl": 0.0, **net})
    return runner.get_owned_session(user.id, sid)


def test_heartbeat_keeps_the_runners_fee_inclusive_pnl_and_counts_returns_with_it():
    user, sid = _session(symbol="KRW-SAND")
    # 83,900 - 83,000 - 매수 수수료 41.5 - 팔 때 수수료 41.95 = 816.55, 실행기 % 는 투입금(수수료 포함) 83,041.5 기준.
    view = _beat(user, sid, unrealized_net=816.55, unrealized_net_pct=0.9833, realized_net=0.0)
    assert view["unrealized_net"] == 816.55 and view["unrealized_net_pct"] == 0.9833 and view["realized_net"] == 0.0
    assert view["fees_included"] is True
    assert abs(view["unrealized_usdt"] - 816.55) < 1e-9
    assert abs(view["return_pct"] - 816.55 / 83_000 * 100) < 1e-3, "총수익률도 수수료 반영 값으로"
    assert view["unrealized_pct"] == 1.0843, "손절 · 익절까지의 거리용 평단 대비 가격은 그대로"


def test_old_runner_without_net_values_falls_back_to_gross_and_says_so():
    user, sid = _session(symbol="KRW-SAND")
    view = _beat(user, sid)
    assert view["unrealized_net_pct"] is None and view["realized_net"] is None
    assert view["fees_included"] is False
    assert abs(view["return_pct"] - 1.0843) < 1e-9


def test_net_values_are_never_mixed_with_gross_ones():
    """평가는 수수료 반영인데 실현은 모르면(수수료를 모르는 체결) 둘 다 수수료 전 — 섞으면 어느 쪽 숫자도 아니다."""
    user, sid = _session(symbol="KRW-SAND")
    view = _beat(user, sid, unrealized_net=816.55, unrealized_net_pct=0.9833, realized_net=None)
    assert view["fees_included"] is False
    assert abs(view["return_pct"] - 1.0843) < 1e-9
    # 진입 수수료를 몰라 평가손익을 못 낸 경우(null)도 마찬가지
    view = _beat(user, sid, unrealized_net=None, unrealized_net_pct=None, realized_net=12.0)
    assert view["fees_included"] is False


def test_flat_session_counts_realized_net_over_invested():
    user, sid = _session(symbol="KRW-SAND", invested_usdt=83_000.0)
    runner.heartbeat(user, sid, {"in_position": False, "position_qty": 0, "entry_price": 0, "last_price": 84.0,
                                 "unrealized_pct": 0.0, "realized_pnl": 1_000.0,
                                 "unrealized_net": 0.0, "unrealized_net_pct": 0.0, "realized_net": 917.0})
    view = runner.get_owned_session(user.id, sid)
    assert view["fees_included"] is True
    assert abs(view["return_pct"] - 917.0 / 83_000 * 100) < 1e-3
    assert abs(view["final_return_pct"] - 917.0 / 83_000 * 100) < 1e-3


def test_stop_report_keeps_the_last_fee_inclusive_position_and_realized():
    user, sid = _session(symbol="KRW-SAND")
    _beat(user, sid, unrealized_net=816.55, unrealized_net_pct=0.9833, realized_net=0.0)
    runner.mark_stopped(user, sid, "stopped", "청산 완료 후 종료", snapshot={
        "in_position": False, "position_qty": 0, "entry_price": 0, "last_price": 83.9, "unrealized_pct": 0.0,
        "realized_pnl": 900.0, "realized_net": 816.55, "unrealized_net": 0.0, "unrealized_net_pct": 0.0})
    view = runner.get_owned_session(user.id, sid)
    assert view["final_unrealized_net_pct"] == 0.9833 and view["final_unrealized_net"] == 816.55
    assert view["final_unrealized_pct"] == 1.0843
    assert view["realized_net"] == 816.55 and view["realized_pnl"] == 900.0
    with get_session() as db:
        assert runner.realized_pnl_text(db.get(RunSession, sid)) == "+817 KRW"


def test_stop_report_from_an_old_runner_leaves_net_values_unknown():
    user, sid = _session(symbol="KRW-SAND")
    _beat(user, sid)
    runner.mark_stopped(user, sid, "stopped", "청산 완료 후 종료", snapshot={
        "in_position": False, "position_qty": 0, "entry_price": 0, "last_price": 83.9, "realized_pnl": 900.0})
    view = runner.get_owned_session(user.id, sid)
    assert view["final_unrealized_net_pct"] is None and view["realized_net"] is None
    assert view["fees_included"] is False and abs(view["final_return_pct"] - 900.0 / 83_000 * 100) < 1e-3


def test_position_invested_is_what_is_in_the_position_now_not_the_session_max():
    """마틴게일처럼 나눠 사면 세션 중 최대 금액(invested_usdt)과 지금 포지션의 투입금이 크게 다르다(2026-10-20)."""
    user, sid = _session(symbol="KRW-GTC")
    runner.heartbeat(user, sid, {"in_position": True, "position_qty": 55.0, "entry_price": 463.4, "last_price": 470.0,
                                 "unrealized_pct": 1.4, "realized_pnl": 0.0, "invested": 25497.7})
    runner.heartbeat(user, sid, {"in_position": False, "position_qty": 0, "entry_price": 0, "last_price": 480.0,
                                 "unrealized_pct": 0.0, "realized_pnl": 900.0, "invested": 0.0})
    view = runner.get_owned_session(user.id, sid)
    assert view["position_invested"] is None, "무포지션이면 없음"
    runner.heartbeat(user, sid, {"in_position": True, "position_qty": 22.0, "entry_price": 463.5, "last_price": 470.0,
                                 "unrealized_pct": 1.4, "realized_pnl": 900.0, "invested": 10199.1})
    view = runner.get_owned_session(user.id, sid)
    assert view["position_invested"] == 10199.1
    assert abs(view["invested_usdt"] - 55.0 * 463.4) < 1e-6, "총수익률 분모는 세션 중 최대 금액 그대로"
    # 옛 실행기는 보내지 않는다 — None(웹이 수량×평단으로)
    runner.heartbeat(user, sid, {"in_position": True, "position_qty": 22.0, "entry_price": 463.5, "last_price": 470.0,
                                 "unrealized_pct": 1.4, "realized_pnl": 900.0})
    assert runner.get_owned_session(user.id, sid)["position_invested"] is None
