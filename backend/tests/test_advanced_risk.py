"""Time-based common-risk controls for the non-candle sims (rules A/B/C).

`daily_max_loss`, `max_holding_hours` and `cooldown_minutes` used to be honored
only by the candle engine (D~J); these tests pin the same semantics onto the
PositionSim (A/B) and DcaSim (C) that the backtest and paper trading share.
"""
from __future__ import annotations

import pandas as pd
import pytest

from engine import Macro, run_backtest
from engine.schema import Fees, Period, Risk
from engine.stepper import make_sim
from tests.test_engine import NO_COST


def _df(closes, freq="D"):
    ts = pd.date_range("2024-01-01", periods=len(closes), freq=freq)
    return pd.DataFrame(
        {
            "timestamp": ts,
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [0.0] * len(closes),
        }
    )


def _macro(rule_type, side, params, risk):
    return Macro(
        symbol="BTCUSDT",
        rule_type=rule_type,
        position_side=side,
        params=params,
        risk=risk,
        period=Period(preset="custom"),
        fees=NO_COST,
    )


# --- max holding time (A long) -----------------------------------------
def test_a_long_max_holding_force_close():
    # TP=10% is never reached, but the 12h cap force-closes on the next daily bar.
    params = {"take_profit_pct": 10.0, "initial_capital": 1000.0}
    risk = Risk(invest_ratio=1.0, max_holding_hours=12)
    res = run_backtest(_macro("A", "long", params, risk), _df([100, 101]))
    assert res.total_trades == 1  # forced exit happened
    assert res.final_return_pct == pytest.approx(1.0, abs=1e-6)

    # Without the cap the position stays open (no round trip recorded).
    res_no = run_backtest(_macro("A", "long", params, Risk(invest_ratio=1.0)), _df([100, 101]))
    assert res_no.total_trades == 0


# --- re-entry cooldown after a stop (A long) ---------------------------
def test_a_long_cooldown_blocks_reentry():
    # Stop at bar1, then a >2-day cooldown blocks the otherwise-profitable re-entry.
    params = {"take_profit_pct": 10.0, "initial_capital": 1000.0}
    closes = [100, 94, 96, 130]  # enter, stop(-6%), would re-enter@96, TP@130
    risk = Risk(invest_ratio=1.0, stop_loss_pct=5.0, cooldown_minutes=3000)
    res = run_backtest(_macro("A", "long", params, risk), _df(closes))
    assert res.total_trades == 1  # only the stop; re-entry was blocked

    no_cd = Risk(invest_ratio=1.0, stop_loss_pct=5.0)
    res_no = run_backtest(_macro("A", "long", params, no_cd), _df(closes))
    assert res_no.total_trades == 2  # stop + a second round trip


# --- daily max loss halt (A long, intraday bars) -----------------------
def test_a_long_daily_max_loss_halts_and_closes():
    # Same-day hourly bars so drawdown is measured within one trading day.
    params = {"take_profit_pct": 100.0, "initial_capital": 1000.0}  # TP unreachable
    closes = [100, 100, 90, 110]  # -10% at bar2 trips the 5% daily cap
    risk = Risk(invest_ratio=1.0, daily_max_loss_pct=5.0)
    res = run_backtest(_macro("A", "long", params, risk), _df(closes, freq="h"))
    assert res.total_trades == 1  # force-closed at the breach
    assert res.final_return_pct == pytest.approx(-10.0, abs=1e-6)  # halted, no recovery

    # No cap: rides the recovery back to +10%, never closes.
    res_no = run_backtest(_macro("A", "long", params, Risk(invest_ratio=1.0)), _df(closes, freq="h"))
    assert res_no.total_trades == 0
    assert res_no.final_return_pct == pytest.approx(10.0, abs=1e-6)


# --- DCA daily max loss stops further buys (rule C) --------------------
def test_dca_daily_max_loss_stops_buys():
    """그날 손실이 한도를 넘으면 그날 예정된 매수를 건너뛴다.

    2026-10-12: 예전에는 시간당 시각 다섯 개로 '세 번 예정된 매수 중 셋째가 막힌다' 를 봤다.
    그때는 바이낸스 적립식이 **스텝 수**로 주기를 셌기 때문인데, 실시간에서 한 스텝은
    3초 틱이라 그게 '3초마다 매수' 가 됐다(실측: 1분에 20회). 이제 거래소와 무관하게
    주기는 '날' 이다. 그래서 실제 모습으로 바꾼다 — 하루에 매수는 한 번이고, 그 한 번이
    오기 **전에** 그날 손실이 한도를 넘으면 건너뛴다.

    C 는 스키마가 일봉을 강제하므로 시간당 봉 자체가 만들 수 없는 조합이기도 했다.
    """
    params = {"amount_per_buy": 100.0, "interval_days": 1}
    risk = Risk(invest_ratio=1.0, daily_max_loss_pct=5.0)
    # 첫 매수를 한낮에 — 그래야 다음 날 '매수 예정 시각' 앞에 그날의 틱이 먼저 온다.
    noon = pd.Timestamp("2024-01-01 12:00", tz="UTC").to_pydatetime()
    day2_open = pd.Timestamp("2024-01-02 00:00", tz="UTC").to_pydatetime()
    day2_drop = pd.Timestamp("2024-01-02 06:00", tz="UTC").to_pydatetime()
    day2_due = pd.Timestamp("2024-01-02 12:00", tz="UTC").to_pydatetime()

    def run(macro):
        sim = make_sim(macro)
        sim.cash = sim.initial_capital = sim._day_start_equity = 300.0
        sim.step(100.0, noon)        # 1일차 매수
        sim.step(100.0, day2_open)   # 2일차 첫 틱 — 그날 기준선이 여기서 잡힌다
        sim.step(80.0, day2_drop)    # -20% → 한도 초과
        sim.step(80.0, day2_due)     # 매수 예정 시각
        return sim

    assert run(_macro("C", "long", params, risk)).buys_done == 1, "그날 매수가 막혀야 한다"
    # 한도가 없으면 2일차 예정 매수가 그대로 들어간다.
    assert run(_macro("C", "long", params, Risk(invest_ratio=1.0))).buys_done == 2


# --- the shared-execution contract still holds when no time rules are set
def test_no_time_rules_is_unchanged_by_timestamps():
    # A macro with none of the advanced fields must backtest identically whether
    # or not timestamps are threaded (they are, now) — i.e. no silent drift.
    params = {"take_profit_pct": 3.0, "initial_capital": 1000.0}
    risk = Risk(invest_ratio=0.7, stop_loss_pct=4.0)
    res = run_backtest(_macro("A", "long", params, risk), _df([100, 103, 98, 106, 101, 110]))
    # Drive the same sim tick-by-tick with NO timestamps (legacy paper contract).
    sim = make_sim(_macro("A", "long", params, risk))
    curve = []
    for c in [100, 103, 98, 106, 101, 110]:
        sim.step(float(c))
        curve.append(round(sim.equity(c), 4))
    assert [round(p.equity, 4) for p in res.equity_curve] == curve
