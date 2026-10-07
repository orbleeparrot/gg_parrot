"""묶음 요약 문구 — 비중과 한도가 요약과 해설에 들어간다."""
from app.engine.backtest import BacktestResult
from app.engine.explain import explain_result
from app.engine.schema import Macro
from app.engine.summary import human_summary

BASE = {
    "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
    "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
}


def test_even_weights_say_even():
    s = human_summary(Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]}))
    assert "균등" in s


def test_uneven_weights_are_listed():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 70},
                                                {"symbol": "ETHUSDT", "weight": 30}]}))
    assert "BTC 70%" in s and "ETH 30%" in s
    assert "균등" not in s


def test_bundle_limit_is_named():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                                {"symbol": "ETHUSDT", "weight": 50}],
                               "bundle_risk": {"max_positions": 1, "max_exposure_pct": 60}}))
    assert "묶음 한도" in s and "한 번에 1종목까지" in s and "총 노출 60% 까지" in s


def test_single_symbol_summary_unchanged():
    s = human_summary(Macro(**BASE))
    assert "묶음 한도" not in s and "균등" not in s


def test_leg_rules_are_named_when_they_differ():
    s = human_summary(Macro(**{**BASE, "legs": [
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_percent": 3.0, "activation_profit": 1.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ]}))
    assert "종목별 규칙" in s


def test_no_leg_rule_means_no_leg_rule_line():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                                {"symbol": "ETHUSDT", "weight": 50}]}))
    assert "종목별 규칙" not in s


def test_limit_line_follows_entry_condition_line():
    s = human_summary(Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 60},
                                                {"symbol": "ETHUSDT", "weight": 40}],
                               "bundle_risk": {"max_positions": 1},
                               "entry_filter": {"kind": "ma", "params": {"period": 20, "side": "above"}}}))
    assert s.index("진입 조건") < s.index("종목 비중") < s.index("묶음 한도")


def _result(**kw):
    base = dict(
        final_return_pct=5.0, buy_hold_return_pct=None, mdd_pct=10.0, total_trades=3,
        win_rate_pct=50.0, max_consecutive_losses=0, liquidation_count=0, sharpe=None,
    )
    return BacktestResult.model_construct(**{**base, **kw})


def test_explain_names_bundle_limit():
    macro = Macro(**{**BASE, "legs": [{"symbol": "BTCUSDT", "weight": 50},
                                      {"symbol": "ETHUSDT", "weight": 50}],
                     "bundle_risk": {"max_positions": 1, "max_exposure_pct": 60}})
    pts = explain_result(macro, _result()).points
    assert any("묶음 한도" in p and "한 번에 1종목까지 · 총 노출 60% 까지" in p for p in pts)


def test_explain_silent_without_bundle_limit():
    macro = Macro(**{**BASE, "symbols": ["BTCUSDT", "ETHUSDT"]})
    pts = explain_result(macro, _result()).points
    assert not any("묶음 한도" in p for p in pts)
