"""묶음을 레그로 나누기 — 금액 · 규칙 · 거절 (2026-10-30).

설계: docs/superpowers/specs/2026-10-30-portfolio-live-run-design.md
"""
from __future__ import annotations

import pytest

from app import runner_legs as RL
from engine import Macro


def _macro(**over) -> Macro:
    body = {
        "symbol": "BTCUSDT", "rule_type": "F", "candle_interval": "1h",
        "market": "spot", "leverage": 1,
        "params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70,
                   "initial_capital": 1_000_000},
        "risk": {"invest_ratio": 1.0, "stop_loss_pct": 3.0},
        "period": {"preset": "3m"},
    }
    body.update(over)
    return Macro(**body)


def _bundle(weights=(50.0, 50.0), symbols=("BTCUSDT", "ETHUSDT"), **over) -> Macro:
    over.setdefault("legs", [{"symbol": sym, "weight": weight}
                             for sym, weight in zip(symbols, weights)])
    return _macro(**over)


# --- 나눈 결과가 단일 종목이다 ------------------------------------------------

def test_a_leg_is_an_ordinary_single_symbol_macro():
    """레그 하나는 지금까지 돌려 온 것과 **똑같은 단일 종목 실행**이어야 한다 —
    실행기의 매매 루프를 안 건드리는 것이 이 설계의 전부다."""
    leg = RL.leg_macro(_bundle(), 1)
    assert leg.symbol == "ETHUSDT"
    assert leg.is_portfolio() is False
    assert leg.legs is None and leg.symbols is None


def test_the_rest_of_the_macro_is_untouched():
    bundle = _bundle(exchange="binance", market="futures", leverage=3, position_side="short",
                     candle_interval="4h")
    leg = RL.leg_macro(bundle, 0)
    assert (leg.exchange, leg.market, leg.leverage) == (bundle.exchange, bundle.market, 3)
    assert leg.position_side == bundle.position_side
    assert leg.candle_interval == "4h"
    assert leg.period.preset == bundle.period.preset


def test_a_single_symbol_macro_cannot_be_split():
    with pytest.raises(ValueError):
        RL.leg_macro(_macro(), 0)
    assert RL.split(_macro())[0].symbol == "BTCUSDT"


def test_a_leg_number_outside_the_bundle_is_refused():
    with pytest.raises(ValueError):
        RL.leg_macro(_bundle(), 2)
    with pytest.raises(ValueError):
        RL.leg_macro(_bundle(), -1)


# --- 금액 ---------------------------------------------------------------------

def test_capital_follows_the_weight():
    bundle = _bundle(weights=(70.0, 30.0))
    assert RL.leg_capital(bundle, 0) == 700_000
    assert RL.leg_capital(bundle, 1) == 300_000
    assert RL.leg_macro(bundle, 0).params["initial_capital"] == 700_000


def test_the_leg_amounts_add_up_to_the_bundle_amount():
    """100/3 은 나누어떨어지지 않는다. 레그마다 따로 반올림하면 합이 어긋난다."""
    bundle = _bundle(weights=(33.3333, 33.3333, 33.3334),
                     symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"))
    shares = [RL.leg_capital(bundle, index) for index in range(3)]
    assert sum(shares) == pytest.approx(1_000_000, abs=1e-6)
    assert all(share > 0 for share in shares)


def test_an_equal_weight_bundle_written_as_symbols_also_splits():
    """물어볼까가 만드는 묶음은 ``symbols`` 모양(균등 비중)이다."""
    bundle = _macro(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"])
    assert RL.leg_count(bundle) == 4
    assert [RL.leg_capital(bundle, i) for i in range(4)] == [250_000] * 4
    assert [m.symbol for m in RL.split(bundle)] == ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]


# --- 레그가 규칙을 덮어쓴 경우 ------------------------------------------------

def test_a_leg_keeps_its_own_rule_and_params():
    bundle = _bundle(legs=[
        {"symbol": "BTCUSDT", "weight": 50},
        {"symbol": "ETHUSDT", "weight": 50, "rule_type": "J",
         "params": {"ma_type": "EMA", "fast_period": 9, "slow_period": 21,
                    "initial_capital": 1_000_000}},
    ])
    first, second = RL.split(bundle)
    assert first.rule_type.value == "F" and first.params["rsi_period"] == 14
    assert second.rule_type.value == "J" and second.params["fast_period"] == 9
    # 묶음의 RSI 칸이 조용히 끼어들면 안 된다 — 다른 규칙을 위해 쓴 값이다.
    assert "rsi_period" not in second.params
    assert second.params["initial_capital"] == 500_000


# --- 묶음 한도는 거절한다 -----------------------------------------------------

def test_a_bundle_with_a_limit_is_refused():
    """레그가 갈라지면 "지금 몇 종목 들고 있나" 를 물을 자리가 없다."""
    bundle = _bundle(bundle_risk={"max_positions": 1})
    assert RL.can_split(bundle) is False
    with pytest.raises(ValueError, match="묶음 한도"):
        RL.leg_macro(bundle, 0)


def test_a_bundle_without_a_limit_can_split():
    assert RL.can_split(_bundle()) is True


def test_the_view_says_how_many_and_why_not():
    view = RL.bundle_view(_bundle(weights=(60.0, 40.0)))
    assert view["size"] == 2 and view["splittable"] is True
    assert [leg["capital"] for leg in view["legs"]] == [600_000, 400_000]
    blocked = RL.bundle_view(_bundle(bundle_risk={"max_exposure_pct": 50}))
    assert blocked["splittable"] is False and blocked["reason"]
    assert RL.bundle_view(_macro()) is None
