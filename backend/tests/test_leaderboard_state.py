"""리더보드 실시간 상태 — 시뮬레이터 state(), 체크포인트 state_json, 행 상태 파생, /api/prices."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engine import Macro
from app.engine.stepper import DcaSim, PositionSim, make_sim


def _macro(rule="A", **over):
    base = {
        "name": "t", "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long",
        "market": "spot", "leverage": 1, "candle_interval": "1h",
        "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "initial_capital": 1000} if rule == "A" else {"amount_per_buy": 100, "interval_days": 1},
        "risk": {"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 30, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


def test_position_sim_state_before_after_entry_and_exit():
    sim = make_sim(_macro("A"), 1_000.0)
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    flat = sim.state()
    assert flat == {"in_position": False, "dir": 1, "qty": 0.0, "entry_price": 0.0, "cooldown_until_ms": None, "halted_today": False}
    sim.step(100.0, t0)  # rule A enters immediately
    held = sim.state()
    assert held["in_position"] is True and held["qty"] > 0 and held["entry_price"] == 100.0
    sim.step(98.9, t0 + timedelta(minutes=1))  # stop-loss → cooldown 30m
    after = sim.state()
    assert after["in_position"] is False and after["qty"] == 0.0
    assert after["cooldown_until_ms"] == int((t0 + timedelta(minutes=31)).timestamp() * 1000)
    assert after["halted_today"] is False


def test_position_sim_short_dir_and_daily_halt():
    sim = make_sim(_macro("A", position_side="short", market="futures", leverage=2,
                          risk={"stop_loss_pct": 50, "daily_max_loss_pct": 1, "cooldown_minutes": 0, "max_holding_hours": 0}), 1_000.0)
    t0 = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    sim.step(100.0, t0)
    assert sim.state()["dir"] == -1
    sim.step(103.0, t0 + timedelta(minutes=1))  # short loses > 1% of day-start equity → halt
    st = sim.state()
    assert st["in_position"] is False and st["halted_today"] is True
    sim.step(103.0, t0 + timedelta(days=1))  # next day clears the halt
    assert sim.state()["halted_today"] is False


def test_dca_sim_state_tracks_average_cost():
    sim = make_sim(_macro("C"), 1_000.0)
    assert sim.state()["in_position"] is False
    sim.step(100.0, None)
    sim.step(200.0, None)
    st = sim.state()
    assert st["in_position"] is True and st["dir"] == 1
    assert abs(st["entry_price"] - (sim.cost_basis / sim.qty)) < 1e-9
    assert st["cooldown_until_ms"] is None
