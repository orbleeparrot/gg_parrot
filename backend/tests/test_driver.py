"""공용 전략 드라이버 — 페이퍼와 실행기가 같은 코어를 쓴다."""
from datetime import datetime, timezone

from app.engine import Macro
from app.engine.driver import Leg, StrategyDriver
from app.engine.stepper import make_sim

T = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _macro(rule="A", **over):
    base = {
        "symbol": "BTCUSDT", "rule_type": rule, "position_side": "long", "market": "spot", "leverage": 1,
        "candle_interval": "5m", "period": {"preset": "3m"},
        "params": {"take_profit_pct": 2, "initial_capital": 1000} if rule == "A"
                  else {"rsi_period": 2, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000},
        "risk": {"stop_loss_pct": 1, "daily_max_loss_pct": 0, "cooldown_minutes": 0, "max_holding_hours": 0},
        "fees": {"commission_pct": 0, "slippage_pct": 0},
    }
    base.update(over)
    return Macro.model_validate(base)


def _driver(macro, initial=1000.0):
    return StrategyDriver([Leg(macro.symbol, make_sim(macro, initial_capital=initial), initial)], initial, macro=macro)


def test_tick_steps_sim_and_notes_fill():
    d = _driver(_macro("A"))
    fill = d.tick(100.0, T)
    assert fill.side == "buy" and d.trade_count == 1 and d.last_fill["kind"] == "" and d.entry_returns["BTCUSDT"] == 0.0
    assert d.tick(98.0, T).side == "sell"
    assert d.trade_count == 2 and d.last_fill["kind"] == "sl" and "BTCUSDT" not in d.entry_returns
    assert d.last_price == 98.0 and d.equity < 1000.0 and d.ret < 0


def test_candle_keys_and_push_candle():
    tick = _driver(_macro("A"))
    assert tick.is_candle_based() is False and tick.candle_keys() == []
    assert tick.push_candle("BTCUSDT", (0, 100, 100, 100, 100)) == 0  # 틱형 sim 에는 on_candle 이 없다
    cd = _driver(_macro("F"))
    assert cd.is_candle_based() is True and cd.candle_keys() == [("BTCUSDT", "5m", "spot")]
    # RSI(2) 는 세 번째 변화부터 값이 나온다 — 100→90→80→70 에서 30 아래로 떨어져 매수 1건.
    assert [cd.push_candle("BTCUSDT", (0, c, c, c, c)) for c in [100, 90, 80, 70]] == [0, 0, 0, 1]
    assert cd.tick(70.0, T).side == "buy"


def test_state_restore_round_trip_and_fill_history():
    d = _driver(_macro("A"))
    d.tick(100.0, T)
    st = d.state()
    assert st["in_position"] is True and st["legs"][0]["symbol"] == "BTCUSDT" and st["trade_count"] == 1
    fresh = _driver(_macro("A"))
    fresh.restore(st, leg_equity={}, total_equity=d.equity)
    fresh.restore_fills([{"symbol": "BTCUSDT", "side": "buy", "return_at_trade": 0.0, "ts": "2026-09-22T00:00:00Z"}])
    assert fresh.state()["in_position"] is True and fresh.trade_count == 1 and fresh.entry_returns == {"BTCUSDT": 0.0}
    assert abs(fresh.sim.equity(100.0) - d.equity) < 1e-6


def test_warmup_feeds_only_candle_legs():
    d = _driver(_macro("F"))
    d.warmup({"BTCUSDT": [(0, 100, 100, 100, 100), (1, 90, 90, 90, 90), (2, 80, 80, 80, 80)]})
    assert d.sim.inner.rsi._count > 0 and d.state()["in_position"] is False
    _driver(_macro("A")).warmup({"BTCUSDT": []})  # 틱형은 무시, 예외 없음
