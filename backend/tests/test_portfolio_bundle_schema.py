"""묶음 스키마 — 비중 · 레그 덮어쓰기 · 묶음 한도의 검증과 펼치기."""
import pytest
from pydantic import ValidationError

from app.engine.schema import BundleRisk, GATEABLE_TYPES, Macro, PortfolioLeg, RuleType

BASE = {
    "symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
    "period": {"preset": "3m"}, "params": {"k": 0.5, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0},
}


def macro(**over):
    return Macro(**{**BASE, **over})


def legs(*pairs):
    return [{"symbol": s, "weight": w} for s, w in pairs]


# --- 비중 -------------------------------------------------------------
def test_weights_must_sum_to_100():
    with pytest.raises(ValidationError, match="비중의 합"):
        macro(legs=legs(("BTCUSDT", 50), ("ETHUSDT", 30)))


def test_weights_sum_100_accepted_and_order_kept():
    m = macro(legs=legs(("ETHUSDT", 60), ("BTCUSDT", 40)))
    assert [leg.symbol for leg in m.legs] == ["ETHUSDT", "BTCUSDT"]
    assert m.symbol == "ETHUSDT"          # 첫 레그가 대표 종목
    assert m.is_portfolio() and m.all_symbols() == ["ETHUSDT", "BTCUSDT"]


def test_weight_rounding_tolerance():
    """33.33 × 3 = 99.99 는 받는다. 0.01 보다 더 벗어나면 거절."""
    macro(legs=legs(("BTCUSDT", 33.33), ("ETHUSDT", 33.33), ("SOLUSDT", 33.34)))
    with pytest.raises(ValidationError, match="비중의 합"):
        macro(legs=legs(("BTCUSDT", 33.3), ("ETHUSDT", 33.3), ("SOLUSDT", 33.3)))


def test_weight_must_be_positive_and_at_most_100():
    with pytest.raises(ValidationError):
        macro(legs=[{"symbol": "BTCUSDT", "weight": 0}, {"symbol": "ETHUSDT", "weight": 100}])


# --- 레그 목록 자체 ----------------------------------------------------
def test_single_leg_refused():
    with pytest.raises(ValidationError, match="2개 이상"):
        macro(legs=legs(("BTCUSDT", 100)))


def test_duplicate_symbol_refused():
    with pytest.raises(ValidationError, match="같은 종목"):
        macro(legs=legs(("BTCUSDT", 50), ("BTCUSDT", 50)))


def test_more_than_max_symbols_refused():
    many = [("BTCUSDT", 20), ("ETHUSDT", 20), ("SOLUSDT", 20),
            ("XRPUSDT", 20), ("ADAUSDT", 10), ("DOGEUSDT", 10)]
    with pytest.raises(ValidationError, match="최대"):
        macro(legs=legs(*many))


def test_legs_and_symbols_together_refused():
    with pytest.raises(ValidationError, match="함께"):
        macro(symbols=["BTCUSDT", "ETHUSDT"], legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)))


# --- leg_specs: symbols 형태를 균등 비중으로 ---------------------------
def test_leg_specs_from_symbols_is_even():
    m = macro(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"])
    specs = m.leg_specs()
    assert [s.symbol for s in specs] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all(abs(s.weight - 100 / 3) < 1e-9 for s in specs)


def test_leg_specs_single_symbol_is_empty():
    assert macro().leg_specs() == []


# --- 레그 규칙 덮어쓰기 ------------------------------------------------
def test_leg_rule_override_requires_params():
    with pytest.raises(ValidationError, match="세부값"):
        macro(legs=[{"symbol": "BTCUSDT", "weight": 50, "rule_type": "E"},
                    {"symbol": "ETHUSDT", "weight": 50}])


def test_leg_rule_override_expands():
    m = macro(legs=[
        {"symbol": "BTCUSDT", "weight": 70, "rule_type": "E",
         "params": {"trail_percent": 3.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 30},
    ])
    btc = m.for_leg(m.legs[0], 700.0)
    eth = m.for_leg(m.legs[1], 300.0)
    assert btc.rule_type is RuleType.E and btc.symbol == "BTCUSDT"
    assert btc.params["trail_percent"] == 3.0 and btc.params["initial_capital"] == 700.0
    assert eth.rule_type is RuleType.I and eth.params["k"] == 0.5
    assert eth.params["initial_capital"] == 300.0
    for leg_macro in (btc, eth):
        assert leg_macro.legs is None and leg_macro.symbols is None
        assert leg_macro.bundle_risk is None and not leg_macro.is_portfolio()


def test_leg_bad_params_names_the_leg():
    with pytest.raises(ValidationError, match="레그 ETHUSDT"):
        macro(legs=[{"symbol": "BTCUSDT", "weight": 50},
                    {"symbol": "ETHUSDT", "weight": 50, "rule_type": "E",
                     "params": {"trail_percent": -5.0, "initial_capital": 1000}}])


# --- 진입 조건 물려받기 ------------------------------------------------
MA_FILTER = {"kind": "ma", "params": {"period": 20, "side": "above"}}


def test_leg_inherits_bundle_filter_when_rule_unchanged():
    m = macro(entry_filter=MA_FILTER, legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)))
    assert m.leg_filter(m.legs[0]) is not None
    assert m.for_leg(m.legs[0], 500.0).entry_filter is not None


def test_leg_that_changes_rule_does_not_inherit_bundle_filter():
    """다른 규칙을 위해 쓴 조건을 물려받지 않는다 — 조용히 엉뚱한 관문이 붙는 것을 막는다."""
    m = macro(entry_filter=MA_FILTER, legs=[
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "E",
         "params": {"trail_percent": 3.0, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ])
    assert m.leg_filter(m.legs[0]) is None
    assert m.for_leg(m.legs[0], 500.0).entry_filter is None
    assert m.for_leg(m.legs[1], 500.0).entry_filter is not None   # 규칙 안 바꾼 레그는 물려받는다


def test_leg_own_filter_wins():
    m = macro(entry_filter=MA_FILTER, legs=[
        {"symbol": "BTCUSDT", "weight": 50,
         "entry_filter": {"kind": "rsi", "params": {"period": 14, "max": 70}}},
        {"symbol": "ETHUSDT", "weight": 50},
    ])
    assert m.leg_filter(m.legs[0]).kind.value == "rsi"


def test_leg_filter_on_unfilterable_leg_rule_refused():
    with pytest.raises(ValidationError):
        macro(legs=[
            {"symbol": "BTCUSDT", "weight": 50, "rule_type": "D",
             "params": {"lower_price": 100, "upper_price": 200, "grid_count": 5,
                        "per_grid_invest": 10, "initial_capital": 1000},
             "entry_filter": MA_FILTER},
            {"symbol": "ETHUSDT", "weight": 50},
        ])


# --- 묶음 한도 --------------------------------------------------------
def test_bundle_risk_needs_at_least_one_limit():
    with pytest.raises(ValidationError, match="최소 하나"):
        BundleRisk()


def test_bundle_risk_requires_portfolio():
    with pytest.raises(ValidationError, match="2개 이상"):
        macro(bundle_risk={"max_positions": 2})


def test_bundle_risk_accepted_on_gateable_legs():
    m = macro(legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)), bundle_risk={"max_positions": 1})
    assert m.bundle_risk.max_positions == 1 and m.bundle_risk.max_exposure_pct is None


def test_bundle_risk_refused_when_a_leg_is_not_gateable():
    """D(그리드)는 사다리 한 칸을 막으면 짝 없는 매수가 남는다 — 한도를 걸 수 없다."""
    with pytest.raises(ValidationError, match="E~K"):
        macro(legs=[
            {"symbol": "BTCUSDT", "weight": 50, "rule_type": "D",
             "params": {"lower_price": 100, "upper_price": 200, "grid_count": 5,
                        "per_grid_invest": 10, "initial_capital": 1000}},
            {"symbol": "ETHUSDT", "weight": 50},
        ], bundle_risk={"max_positions": 1})


def test_bundle_risk_refused_on_tick_driven_bundle_rule():
    with pytest.raises(ValidationError, match="E~K"):
        Macro(**{**BASE, "rule_type": "A", "params": {"take_profit_pct": 5, "initial_capital": 1000},
                 "legs": legs(("BTCUSDT", 50), ("ETHUSDT", 50)),
                 "bundle_risk": {"max_positions": 1}})


def test_max_positions_at_or_above_leg_count_refused():
    """레그 수와 같은 상한은 아무것도 막지 않는다 — 한도를 걸었다고 착각하게 둘 수 없다."""
    with pytest.raises(ValidationError, match="종목 수보다 작아야"):
        macro(legs=legs(("BTCUSDT", 50), ("ETHUSDT", 50)), bundle_risk={"max_positions": 2})


def test_mixed_rules_allowed_without_bundle_risk():
    """한도가 없으면 규칙을 섞어도 된다(관문이 없으니 D 의 짝 문제도 없다)."""
    m = macro(legs=[
        {"symbol": "BTCUSDT", "weight": 50, "rule_type": "D",
         "params": {"lower_price": 100, "upper_price": 200, "grid_count": 5,
                    "per_grid_invest": 10, "initial_capital": 1000}},
        {"symbol": "ETHUSDT", "weight": 50},
    ])
    assert m.for_leg(m.legs[0], 500.0).rule_type is RuleType.D


def test_gateable_types_matches_filterable():
    from app.engine.schema import FILTERABLE_TYPES
    assert GATEABLE_TYPES == FILTERABLE_TYPES
    assert RuleType.D not in GATEABLE_TYPES and RuleType.E in GATEABLE_TYPES
