from engine import Macro, human_summary
from engine.schema import Risk


def test_summary_short_a():
    m = Macro(
        symbol="BTCUSDT",
        rule_type="A",
        position_side="short",
        params={"take_profit_pct": 5.0, "initial_capital": 1_000_000},
        risk=Risk(invest_ratio=0.5, stop_loss_pct=3.0),
    )
    s = human_summary(m)
    assert "BTC" in s and "숏" in s and "5%" in s and "3%" in s and "50%" in s


def test_summary_long_b():
    m = Macro(
        symbol="ETHUSDT",
        rule_type="B",
        position_side="long",
        params={"buy_price": 2000, "sell_price": 2500, "initial_capital": 1000},
    )
    s = human_summary(m)
    assert "ETH" in s and "롱" in s and "2,000" in s and "2,500" in s


def test_summary_dca():
    m = Macro(
        symbol="BTCUSDT",
        rule_type="C",
        position_side="long",
        params={"amount_per_buy": 100000, "interval_days": 7},
    )
    s = human_summary(m)
    assert "7일마다" in s and "DCA" in s
