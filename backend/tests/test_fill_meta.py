"""Fill 에 사유·직전 수량이 붙는다 — 실행기 명령 변환(qty_frac)과 로그 문구의 근거."""
from datetime import datetime, timezone

from app.engine import Macro
from app.engine.stepper import make_sim


def _a():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "A", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


def _rsi():
    return Macro.model_validate({
        "symbol": "BTCUSDT", "rule_type": "F", "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 0, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    })


def test_position_sim_fill_carries_reason_and_qty_before():
    sim = make_sim(_a(), 1000.0)
    t = datetime(2026, 9, 22, tzinfo=timezone.utc)
    entry = sim.step(100.0, t)
    assert entry.side == "buy" and entry.reason == "진입" and entry.qty_before == 0.0
    exit_ = sim.step(98.0, t)  # 손절
    assert exit_.side == "sell" and exit_.reason == "손절"
    assert abs(exit_.qty_before - entry.qty) < 1e-12 and abs(exit_.qty - exit_.qty_before) < 1e-12


def test_candle_sim_indicator_reason_names_the_signal():
    from app.engine.candles import make_candle_sim

    inner = make_candle_sim(_rsi(), initial_capital=1000.0)
    t = datetime(2026, 9, 22, tzinfo=timezone.utc)
    closes = [100, 90, 80, 70, 60]  # RSI(2) 가 30 아래로
    fills = []
    for c in closes:
        fills += inner.on_candle(c, c, c, c, t)
    for c in [61, 70, 90, 120, 150]:
        fills += inner.on_candle(c, c, c, c, t)
    sides = [f.side for f in fills]
    assert sides[:2] == ["buy", "sell"]
    assert fills[0].reason.startswith("RSI ") and fills[0].reason.endswith("· 진입")
    assert fills[1].reason.startswith("RSI ") and fills[1].reason.endswith("· 청산")
    assert abs(fills[1].qty_before - fills[0].qty) < 1e-9
