"""체결 기준 장부(fill_ledger_v1) 작업 동안 백테스트 · 모의 결과가 한 줄도 바뀌지 않는지 지킨다(2026-10-21).

장부 작업은 CandleSim 공용 코드(청산 · 포지션 끝 처리 · 진입)를 고친다. 정책이 꺼진 sim — 백테스트 · 모의 · 옛 실행기
세션 — 은 지금과 똑같이 돌아야 한다. 결정적 봉 720개를 규칙 7개에 넣어 나온 체결 전부 · 실현손익 · 마지막 자산을
fixture 와 대조한다. 값이 바뀌면 fixture 를 다시 쓰지 말고 원인을 찾는다.

fixture 다시 쓰기(의도한 변경일 때만): ``GOLDEN_WRITE=1 pytest tests/test_fill_ledger_golden.py``
"""
import json
import math
import os
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from app.engine import Macro
from app.engine.candles import make_candle_sim

FIXTURE = pathlib.Path(__file__).resolve().parent / "fixtures" / "fill_ledger_golden.json"
T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)
FEES = {"commission_pct": 0.05, "slippage_pct": 0.05}


def _base(rule: str, params: dict, risk: dict | None = None) -> dict:
    return {"symbol": "BTCUSDT", "rule_type": rule, "position_side": "long", "market": "spot", "leverage": 1,
            "candle_interval": "1h", "period": {"preset": "3m"}, "params": params,
            "risk": {"invest_ratio": 1.0, **(risk or {})}, "fees": FEES}


MACROS = {
    "H_basic": _base("H", {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 1.0,
                           "take_profit": 2.0, "max_safety_orders": 3, "initial_capital": 1000}),
    "H_scaled_stop": _base("H", {"base_order_size": 100, "safety_order_size": 80, "price_deviation": 1.5,
                                 "safety_order_step_scale": 1.2, "safety_order_volume_scale": 1.5,
                                 "max_safety_orders": 4, "take_profit": 1.5, "initial_capital": 2000},
                           {"stop_loss_pct": 5.0, "cooldown_minutes": 30}),
    "H_hold_daily": _base("H", {"base_order_size": 150, "safety_order_size": 150, "price_deviation": 2.0,
                                "take_profit": 3.0, "max_safety_orders": 2, "initial_capital": 1000},
                          {"max_holding_hours": 24, "daily_max_loss_pct": 3.0}),
    "D_grid": _base("D", {"lower_price": 90, "upper_price": 115, "grid_count": 10, "initial_capital": 1000}),
    "E_trailing": _base("E", {"entry_mode": "immediate", "activation_profit": 3.0, "trail_percent": 2.0,
                              "reenter_after_exit": True, "initial_capital": 1000}),
    "I_breakout": _base("I", {"k": 0.5, "exit_mode": "next_open", "initial_capital": 1000}),
    "F_rsi": _base("F", {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70, "initial_capital": 1000}),
}


def _path(i: int) -> float:
    """완만한 상승 → 급락 −18% → 반등 → 횡보. 난수 없이 sin 과 고정 패턴만 쓴다."""
    if i < 200:
        return 100 + 12 * i / 200 + 1.5 * math.sin(i * 2 * math.pi / 24)
    if i < 260:
        top = 112 + 1.5 * math.sin(200 * 2 * math.pi / 24)
        return top * (1 - 0.18 * (i - 200) / 60) + 0.8 * math.sin(i * 2 * math.pi / 7)
    if i < 460:
        bottom = 112 * 0.82
        return bottom + (108 - bottom) * (i - 260) / 200 + 1.2 * math.sin(i * 2 * math.pi / 30)
    return 105 + 3 * math.sin(i * 2 * math.pi / 36) + 0.6 * math.sin(i * 2 * math.pi / 5)


def candles(n: int = 720) -> list:
    rows, prev = [], _path(0)
    for i in range(n):
        o, c = prev, _path(i)
        up = 0.004 + 0.006 * ((i * 7) % 5) / 4  # 위 꼬리 0.4~1.0%
        down = 0.004 + 0.008 * ((i * 3) % 7) / 6  # 아래 꼬리 0.4~1.2%
        h, l = max(o, c) * (1 + up), min(o, c) * (1 - down)
        rows.append((T0 + timedelta(hours=i), o, h, l, c, 1000.0 + (i % 10) * 50.0))
        prev = c
    return rows


def _sig(x: float) -> float:
    return float(f"{float(x):.12g}")


def run(body: dict) -> dict:
    sim = make_candle_sim(Macro.model_validate(body))
    fills = []
    bars = candles()
    for i, (ts, o, h, l, c, v) in enumerate(bars):
        for f in sim.on_candle(o, h, l, c, ts, volume=v):
            fills.append([i, f.side, _sig(f.price), _sig(f.qty), f.reason, _sig(f.qty_before)])
    last = bars[-1][4]
    return {"fills": fills, "closed": [_sig(p) for p in sim.closed_trades], "same_bar_sl": sim.same_bar_sl,
            "cash": _sig(sim.cash), "equity": _sig(sim.equity(last)), "in_position": bool(sim.in_position())}


def test_fixture_covers_every_macro_and_trades_happen():
    """fixture 가 비거나 체결이 하나도 없으면 아무것도 지키지 못한다."""
    want = json.loads(FIXTURE.read_text())
    assert set(want) == set(MACROS)
    for name, got in want.items():
        assert got["fills"], name


@pytest.mark.parametrize("name", sorted(MACROS))
def test_backtest_unchanged(name):
    got = run(MACROS[name])
    if os.environ.get("GOLDEN_WRITE") == "1":
        data = json.loads(FIXTURE.read_text()) if FIXTURE.exists() else {}
        data[name] = got
        FIXTURE.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return
    want = json.loads(FIXTURE.read_text())[name]
    assert got == want
