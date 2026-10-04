"""거래 집중도 — 상위 세 거래가 수익에서 차지하는 몫. 기존 값은 그대로여야 한다."""
import math

import pandas as pd

from app.engine import Macro, run_backtest
from app.engine.backtest import _summarize_trade_share


def test_a_few_big_winners_show_a_high_share():
    assert _summarize_trade_share([80.0, 10.0, 5.0, 3.0, 2.0]) == 95.0


def test_evenly_spread_wins_show_a_low_share():
    assert _summarize_trade_share([10.0] * 20) == 15.0


def test_losses_do_not_count_toward_the_denominator():
    assert _summarize_trade_share([50.0, 50.0, -40.0]) == 100.0


def test_no_winning_trades_yields_none():
    assert _summarize_trade_share([]) is None
    assert _summarize_trade_share([-5.0, -3.0]) is None


def test_an_overflowing_total_is_not_measured_instead_of_nan():
    """1e308 을 몇 번 더하면 inf — 몫이 nan 이 되면 응답 직렬화가 500 으로 터진다."""
    assert _summarize_trade_share([1e308] * 4) is None


def test_the_engine_result_carries_the_share_for_a_run_with_winning_trades():
    """_metrics 가 결과에 값을 실어 보낸다 — 기본값 None 이 그대로 나오지 않는다."""
    rows = 120
    close = [100.0 + 25.0 * math.sin(i / 9.0) + i * 0.05 for i in range(rows)]
    frame = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=rows, freq="1D", tz="UTC"),
                          "open": close, "high": [c * 1.02 for c in close],
                          "low": [c * 0.98 for c in close], "close": close, "volume": [10.0] * rows})
    macro = Macro(symbol="BTCUSDT", rule_type="A", candle_interval="1d",
                  params={"take_profit_pct": 5, "initial_capital": 1_000_000}, risk={"stop_loss_pct": 3})
    share = run_backtest(macro, frame).top_trade_share_pct
    assert share is not None and 0 < share <= 100
