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


@pytest.mark.parametrize("rule_type", ["A", "B", "C", "D"])
def test_filter_rejection_runs_before_params_validation(rule_type):
    """params 가 비어 있어도 필터 때문에 거부된다 — 검사가 params 분기보다 앞이라는 증거."""
    body = {**BREAKOUT, "rule_type": rule_type, "params": {}, "entry_filter": MA20}
    if rule_type == "C":
        body["candle_interval"] = "1d"   # C 는 이 검사가 필터 검사보다 앞에 있다
    with pytest.raises(ValueError, match="does not support entry_filter"):
        Macro(**body)


@pytest.mark.parametrize("bad", [
    {"kind": "ma", "params": {"period": 1, "side": "above"}},        # period < 2
    {"kind": "ma", "params": {"period": 401, "side": "above"}},      # period > 400
    {"kind": "ma", "params": {"period": 20, "side": "sideways"}},    # 모르는 side
    {"kind": "ma", "params": {"period": 20}},                        # side 없음
    {"kind": "ma", "params": {"ma_type": "WMA", "period": 20, "side": "above"}},  # 모르는 ma_type
    {"kind": "rsi", "params": {"period": 14}},                       # min·max 둘 다 없음
    {"kind": "rsi", "params": {"period": 14, "min": 70, "max": 30}}, # min > max
    {"kind": "rsi", "params": {"period": 1, "max": 70}},             # period < 2
    {"kind": "rsi", "params": {"period": 201, "max": 70}},           # period > 200
    {"kind": "rsi", "params": {"period": 14, "min": -1}},            # min < 0
    {"kind": "rsi", "params": {"period": 14, "min": 101}},           # min > 100
    {"kind": "rsi", "params": {"period": 14, "max": -1}},            # max < 0
    {"kind": "rsi", "params": {"period": 14, "max": 101}},           # max > 100
    {"kind": "bb", "params": {"period": 20, "zone": "middle"}},      # 모르는 zone
    {"kind": "bb", "params": {"period": 1, "zone": "inside"}},       # period < 2
    {"kind": "bb", "params": {"period": 401, "zone": "inside"}},     # period > 400
    {"kind": "bb", "params": {"num_std": 0, "zone": "inside"}},      # num_std <= 0
    {"kind": "bb", "params": {"num_std": 5.1, "zone": "inside"}},    # num_std > 5
    {"kind": "bb", "params": {"period": 20}},                        # zone 없음
    {"kind": "volume", "params": {"period": 20}},                    # multiple 없음
    {"kind": "volume", "params": {"period": 20, "multiple": 0}},     # multiple <= 0
    {"kind": "volume", "params": {"period": 1, "multiple": 2}},      # period < 2
    {"kind": "volume", "params": {"period": 401, "multiple": 2}},    # period > 400
    {"kind": "volume", "params": {"period": 20, "multiple": 101}},   # multiple > 100
    {"kind": "nope", "params": {}},                                  # 모르는 kind
])
def test_bad_filter_params_are_rejected(bad):
    with pytest.raises(ValueError):
        Macro(**{**BREAKOUT, "entry_filter": bad})


@pytest.mark.parametrize("good", [
    {"kind": "ma", "params": {"ma_type": "EMA", "period": 400, "side": "below"}},
    {"kind": "ma", "params": {"period": 2, "side": "above"}},                  # 하한 경계
    {"kind": "rsi", "params": {"period": 2, "min": 0, "max": 100}},            # 경계 값 전부
    {"kind": "rsi", "params": {"period": 200, "max": 70}},
    {"kind": "rsi", "params": {"period": 14, "min": 50, "max": 50}},           # min == max 는 허용(> 만 오류)
    {"kind": "bb", "params": {"period": 2, "num_std": 5, "zone": "inside"}},
    {"kind": "bb", "params": {"period": 400, "num_std": 0.1, "zone": "above_upper"}},
    {"kind": "volume", "params": {"period": 2, "multiple": 100}},
    {"kind": "volume", "params": {"period": 400, "multiple": 0.5}},
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


def test_rsi_min_equal_to_max_is_accepted():
    """min > max 만 오류다 — >= 로 바뀌면 이 시험이 잡는다."""
    m = Macro(**{**BREAKOUT, "entry_filter": {"kind": "rsi", "params": {"period": 14, "min": 50, "max": 50}}})
    assert (m.entry_filter.params["min"], m.entry_filter.params["max"]) == (50, 50)
