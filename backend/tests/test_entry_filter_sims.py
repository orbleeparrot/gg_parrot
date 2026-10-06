"""필터가 일곱 규칙의 진입을 막는다 — 그리고 청산·세이프티오더는 막지 않는다."""
import pytest
from datetime import datetime, timedelta, timezone

from app.engine.candles import make_candle_sim
from app.engine.schema import Macro

START = datetime(2026, 1, 1, tzinfo=timezone.utc)

BASE = {"symbol": "BTCUSDT", "candle_interval": "1h", "period": {"preset": "3m"},
        "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}
RULES = {
    "E": {"trail_percent": 3.0, "initial_capital": 1000},
    "F": {"rsi_period": 2, "entry_threshold": 90, "exit_threshold": 95, "initial_capital": 1000},
    "G": {"bb_period": 3, "bb_std": 1.0, "initial_capital": 1000},
    "H": {"base_order_size": 100, "safety_order_size": 100, "price_deviation": 1.0,
          "take_profit": 1.0, "max_safety_orders": 2, "initial_capital": 1000},
    "I": {"k": 0.1, "initial_capital": 1000},
    "J": {"fast_period": 2, "slow_period": 3, "initial_capital": 1000},
    "K": {"drop_trigger_pct": 5, "short_take_profit_pct": 3, "short_stop_loss_pct": 2,
          "initial_capital": 1000},
}
# 절대 통과하지 못하는 필터: 400봉 이평은 40봉만 주면 영원히 안 데워지고, 안 데워진 필터는
# 막는다(원칙 2). 가격 열과 무관하게 결정적이라 일곱 규칙을 같은 필터로 쓸 수 있다.
BLOCKS = {"kind": "ma", "params": {"period": 400, "side": "above"}}


def _macro(rule_type, entry_filter=None):
    body = {**BASE, "rule_type": rule_type, "params": RULES[rule_type]}
    if rule_type == "K":
        body["market"] = "futures"
    if entry_filter is not None:
        body["entry_filter"] = entry_filter
    return Macro(**body)


def _run(macro, closes):
    sim = make_candle_sim(macro)
    fills = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        fills += sim.on_candle(o, max(o, c) * 1.01, min(o, c) * 0.99, c, START + timedelta(hours=i))
    return fills


RISING = [100.0 + i * 5 for i in range(40)]
# 삼각파 — 올랐다 내린다. F(RSI)·G(볼린저 역추세) 는 계속 오르는 열에서는 필터가 없어도
# 진입하지 않는다(RSI 가 100 에 붙고, 역추세는 하단을 뚫을 일이 없다). 그래서 따로 준다.
TRIANGLE = ([80.0, 100.0, 120.0, 140.0, 120.0, 100.0] * 7)[:40]
SERIES = {"F": TRIANGLE, "G": TRIANGLE}


def _series(rule_type):
    return SERIES.get(rule_type, RISING)


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_filter_blocks_every_entry_on_each_filterable_rule(rule_type):
    closes = _series(rule_type)
    bare = _run(_macro(rule_type), closes)
    filtered = _run(_macro(rule_type, BLOCKS), closes)
    assert not [f for f in filtered if f.side in ("buy", "short")], rule_type
    # 필터 없이는 뭔가 사야 한다 — 아니면 이 시험은 아무것도 증명하지 않는다.
    assert [f for f in bare if f.side in ("buy", "short")], rule_type


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_macro_without_filter_is_byte_for_byte_unchanged(rule_type):
    closes = _series(rule_type)
    before = [(f.side, round(f.price, 6), round(f.qty, 8)) for f in _run(_macro(rule_type), closes)]
    again = [(f.side, round(f.price, 6), round(f.qty, 8)) for f in _run(_macro(rule_type), closes)]
    assert before == again and before  # 결정적이고, 비어 있지 않다


def test_filter_does_not_block_exits():
    """포지션을 들고 있고 필터가 거짓인 동안에도 손절이 일어난다."""
    macro = Macro(**{**BASE, "rule_type": "E", "params": RULES["E"],
                     "risk": {"invest_ratio": 1.0, "stop_loss_pct": 2.0},
                     "entry_filter": {"kind": "ma", "params": {"period": 3, "side": "above"}}})
    # 오르는 구간에서 진입(필터 통과) -> 급락 구간에서 필터는 거짓이지만 손절은 나가야 한다.
    closes = [100.0 + i * 5 for i in range(10)] + [100.0 - i * 5 for i in range(10)]
    fills = _run(macro, closes)
    assert [f for f in fills if f.side == "buy"]
    assert [f for f in fills if f.side == "sell"]


def test_filter_does_not_block_safety_orders():
    """H 첫 진입 뒤 필터가 거짓으로 바뀌어도 세이프티오더는 나간다."""
    macro = _macro("H", {"kind": "ma", "params": {"period": 3, "side": "above"}})
    closes = [100.0 + i * 5 for i in range(8)] + [135.0 - i * 3 for i in range(14)]
    buys = [f for f in _run(macro, closes) if f.side == "buy"]
    assert len(buys) >= 2, buys  # 첫 진입 + 세이프티오더 최소 하나


def test_rule_local_filter_and_entry_filter_both_apply():
    """I 의 ma_filter_period 와 entry_filter 가 각각 독립적으로 막는다."""
    # entry_filter 만 거짓 -> 안 산다
    only_new = _macro("I", {"kind": "ma", "params": {"period": 3, "side": "below"}})
    assert not [f for f in _run(only_new, RISING) if f.side == "buy"]
    # ma_filter_period 만 거짓(내리는 봉) -> 안 산다
    falling = [200.0 - i * 5 for i in range(40)]
    only_old = Macro(**{**BASE, "rule_type": "I", "market": "spot",
                        "params": {**RULES["I"], "ma_filter_period": 3},
                        "entry_filter": {"kind": "ma", "params": {"period": 3, "side": "below"}}})
    assert not [f for f in _run(only_old, falling) if f.side == "buy"]


def test_unwarmed_filter_blocks_until_the_period_is_reached():
    macro = _macro("I", {"kind": "ma", "params": {"period": 30, "side": "above"}})
    assert not [f for f in _run(macro, RISING[:12]) if f.side == "buy"]
    # 같은 필터, 충분한 봉 -> 데워지고 통과한다. 이 줄이 없으면 위 단정은 "필터가 아예 망가져도 통과" 다.
    assert [f for f in _run(macro, RISING) if f.side == "buy"]
