"""진입 필터 스키마 — kind 별 params 검증, 못 쓰는 규칙 거부."""
import pytest

from app.engine.schema import FILTERABLE_TYPES, Macro, RuleType

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}
MA20 = {"kind": "ma", "params": {"period": 20, "side": "above"}}


def test_filterable_types_are_the_seven_candle_rules():
    assert FILTERABLE_TYPES == frozenset({RuleType.E, RuleType.F, RuleType.G,
                                          RuleType.H, RuleType.I, RuleType.J, RuleType.K})


def test_no_filter_stays_none():
    assert Macro(**BREAKOUT).entry_filter is None


def test_ma_params_are_canonicalized():
    m = Macro(**{**BREAKOUT, "entry_filter": MA20})
    assert m.entry_filter.params == {"ma_type": "SMA", "period": 20, "side": "above"}


def test_unknown_param_keys_are_dropped_like_rule_params():
    m = Macro(**{**BREAKOUT, "entry_filter": {"kind": "ma", "params": {**MA20["params"], "nope": 1}}})
    assert "nope" not in m.entry_filter.params


@pytest.mark.parametrize("rule_type, params, extra", [
    ("A", {"take_profit_pct": 3.0, "initial_capital": 1000}, {}),
    ("B", {"buy_price": 1, "sell_price": 2, "initial_capital": 1000}, {}),
    ("C", {"amount_per_buy": 100, "interval_days": 1, "initial_capital": 1000}, {"candle_interval": "1d"}),
    ("D", {"lower_price": 1, "upper_price": 2, "grid_count": 3, "initial_capital": 1000}, {}),
])
def test_filter_on_unfilterable_rule_is_rejected(rule_type, params, extra):
    body = {**BREAKOUT, "rule_type": rule_type, "params": params, **extra, "entry_filter": MA20}
    with pytest.raises(ValueError, match="entry_filter"):
        Macro(**body)


@pytest.mark.parametrize("bad", [
    {"kind": "ma", "params": {"period": 1, "side": "above"}},        # period < 2
    {"kind": "ma", "params": {"period": 401, "side": "above"}},      # period > 400
    {"kind": "ma", "params": {"period": 20, "side": "sideways"}},    # 모르는 side
    {"kind": "ma", "params": {"period": 20}},                        # side 없음
    {"kind": "rsi", "params": {"period": 14}},                       # min·max 둘 다 없음
    {"kind": "rsi", "params": {"period": 14, "min": 70, "max": 30}}, # min > max
    {"kind": "bb", "params": {"period": 20, "zone": "middle"}},      # 모르는 zone
    {"kind": "volume", "params": {"period": 20}},                    # multiple 없음
    {"kind": "volume", "params": {"period": 20, "multiple": 0}},     # multiple <= 0
    {"kind": "nope", "params": {}},                                  # 모르는 kind
])
def test_bad_filter_params_are_rejected(bad):
    with pytest.raises(ValueError):
        Macro(**{**BREAKOUT, "entry_filter": bad})


@pytest.mark.parametrize("good", [
    {"kind": "ma", "params": {"ma_type": "EMA", "period": 400, "side": "below"}},
    {"kind": "rsi", "params": {"period": 14, "max": 70}},
    {"kind": "rsi", "params": {"period": 14, "min": 30}},
    {"kind": "bb", "params": {"period": 20, "num_std": 2.0, "zone": "below_lower"}},
    {"kind": "volume", "params": {"period": 20, "multiple": 2.0}},
])
def test_good_filter_params_are_accepted(good):
    assert Macro(**{**BREAKOUT, "entry_filter": good}).entry_filter is not None


def test_filter_survives_a_round_trip():
    m = Macro(**{**BREAKOUT, "entry_filter": MA20})
    assert Macro.model_validate_json(m.model_dump_json()).entry_filter.params["period"] == 20
