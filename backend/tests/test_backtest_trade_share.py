"""거래 집중도 — 상위 세 거래가 수익에서 차지하는 몫. 기존 값은 그대로여야 한다."""
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
