"""필터가 일곱 규칙의 진입을 막는다 — 그리고 청산·세이프티오더·방어 숏은 막지 않는다."""
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
MA3_ABOVE = {"kind": "ma", "params": {"period": 3, "side": "above"}}


def _macro(rule_type, entry_filter=None, extra_params=None):
    params = {**RULES[rule_type], **(extra_params or {})}
    body = {**BASE, "rule_type": rule_type, "params": params}
    if rule_type == "K":
        body["market"] = "futures"
    if entry_filter is not None:
        body["entry_filter"] = entry_filter
    return Macro(**body)


def _run_indexed(macro, closes, volumes=None):
    """``(봉 index, Fill)`` 목록. 어느 봉에서 일어난 체결인지 알아야 관문 상태와 맞춰 볼 수 있다."""
    sim = make_candle_sim(macro)
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        got = sim.on_candle(o, max(o, c) * 1.01, min(o, c) * 0.99, c, START + timedelta(hours=i),
                            volume=None if volumes is None else volumes[i])
        rows += [(i, f) for f in got]
    return rows


def _run(macro, closes, volumes=None):
    return [f for _, f in _run_indexed(macro, closes, volumes)]


# --- 관문 오라클 ---------------------------------------------------------
# 구현의 allows() 를 읽으면 "끈끈해진 allows()" 같은 변형이 단정까지 함께 오염시킨다.
# 그래서 기대 관문 상태를 봉 열에서 손으로 다시 계산한다. 봉 i 의 진입 판단은 직전 마감봉(i-1)까지만 본다.
def _ma_gate(closes, period=3, side="above"):
    """``period`` 봉 이평 관문. 창이 차려면 i-1 >= period-1, 즉 i >= period 다."""
    out = []
    for i in range(len(closes)):
        if i < period:
            out.append(False)
            continue
        avg = sum(closes[i - period:i]) / period
        out.append(closes[i - 1] > avg if side == "above" else closes[i - 1] < avg)
    return out


# 데워진 뒤 열리고 닫히는 관문을 **가격과 무관하게** 만들려고 거래량 필터를 쓴다. 가격 기반 필터는
# 규칙의 진입 조건과 상관이 생겨(예: 볼린저 하단 매수 + "이평 위") 어떤 규칙은 아예 진입하지 못한다.
WARM = {"kind": "volume", "params": {"period": 2, "multiple": 2.0}}
# 세 봉마다 한 번 거래량이 10배 — 직전 2봉 평균의 2배를 넘는 봉이 주기적으로 생긴다.
WARM_VOLUMES = [100.0 if i % 3 == 2 else 10.0 for i in range(40)]
# 네 봉 톱니 — 일곱 규칙 전부가 봉 열 안에서 여러 번 진입하려 한다(관문이 없으면).
WARM_CLOSES = ([100.0, 110.0, 120.0, 110.0] * 10)[:40]
# F 의 기본 설정(진입 RSI ≤ 90)은 사실상 늘 참이라 한 번 들어가고 끝난다 — 되풀이되는 진입이 필요하다.
WARM_PARAMS = {"F": {"entry_threshold": 30, "exit_threshold": 70}}


def _warm_gate(volumes):
    """``WARM`` 관문을 손으로 계산한다 — period=2 라 기준은 i-3·i-2 봉, 판정 대상은 i-1 봉이다."""
    out = []
    for i in range(len(volumes)):
        if i < 3:
            out.append(False)       # 직전 창이 아직 안 찼다 -> 모르면 막는다
            continue
        baseline = (volumes[i - 3] + volumes[i - 2]) / 2.0
        out.append(volumes[i - 1] >= baseline * 2.0)
    return out


def _gated_entries(rows):
    """관문을 거치는 진입만 골라낸다 — 포지션이 비어 있을 때의 롱 진입.

    관문을 **거치지 않는** 주문(H 세이프티오더 · K 방어 숏)은 이미 잡은 포지션을 정리하는 쪽이라
    설계상 막지 않는다. 세이프티오더는 ``qty_before > 0`` 로, K 방어 숏은 ``side == "short"`` 로 갈린다.
    """
    return [(i, f) for i, f in rows if f.side == "buy" and f.qty_before == 0.0]


RISING = [100.0 + i * 5 for i in range(40)]
# 삼각파 — 올랐다 내린다. F(RSI)·G(볼린저 역추세) 는 계속 오르는 열에서는 필터가 없어도
# 진입하지 않는다(RSI 가 100 에 붙고, 역추세는 하단을 뚫을 일이 없다). 그래서 따로 준다.
TRIANGLE = ([80.0, 100.0, 120.0, 140.0, 120.0, 100.0] * 7)[:40]
SERIES = {"F": TRIANGLE, "G": TRIANGLE}


def _series(rule_type):
    return SERIES.get(rule_type, RISING)


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_filter_blocks_every_entry_on_each_filterable_rule(rule_type):
    """차가운 필터(끝까지 안 데워진다)는 일곱 규칙의 진입을 전부 막는다 — 원칙 2."""
    closes = _series(rule_type)
    bare = _run(_macro(rule_type), closes)
    filtered = _run(_macro(rule_type, BLOCKS), closes)
    assert not [f for f in filtered if f.side in ("buy", "short")], rule_type
    # 필터 없이는 뭔가 사야 한다 — 아니면 이 시험은 아무것도 증명하지 않는다.
    assert [f for f in bare if f.side in ("buy", "short")], rule_type


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_warmed_filter_that_evaluates_false_still_blocks_entries(rule_type):
    """데워진 **뒤** 거짓이 된 관문도 진입을 막는다 — 차가운 관문만 돌리면 이게 통째로 미검증이다.

    관문이 한 번 열린 뒤 다시 닫히는 열을 준다. 기대 관문 상태는 ``_warm_gate`` 가 거래량 열에서
    손으로 계산하므로, 구현의 ``allows()`` 가 "한 번 참이면 영원히 참" 으로 망가지면
    관문이 닫힌 봉의 진입이 그대로 드러난다.
    """
    closes = WARM_CLOSES
    volumes = WARM_VOLUMES[:len(closes)]
    gate = _warm_gate(volumes)
    # 이 열이 '데워졌다가 뒤집힌다' 는 것을 먼저 확인한다 — 아니면 아래 단정이 차가운 경우의 반복이다.
    assert any(gate), "관문이 한 번도 열리지 않는다"
    assert not all(gate[3:]), "관문이 열린 뒤 닫히지 않는다"

    macro = _macro(rule_type, WARM, WARM_PARAMS.get(rule_type))
    entries = _gated_entries(_run_indexed(macro, closes, volumes))
    # 양성 대조: 관문이 열린 봉에서는 들어간다. 없으면 "전부 막는" 구현도 아래 단정을 통과한다.
    assert entries, rule_type
    blocked_bars = [i for i, _ in entries if not gate[i]]
    assert not blocked_bars, (rule_type, blocked_bars)


@pytest.mark.parametrize("rule_type", sorted(RULES))
def test_macro_without_filter_is_byte_for_byte_unchanged(rule_type):
    closes = _series(rule_type)
    before = [(f.side, round(f.price, 6), round(f.qty, 8)) for f in _run(_macro(rule_type), closes)]
    again = [(f.side, round(f.price, 6), round(f.qty, 8)) for f in _run(_macro(rule_type), closes)]
    assert before == again and before  # 결정적이고, 비어 있지 않다


def test_dip_reference_price_is_anchored_regardless_of_the_filter():
    """E 눌림목 기준가는 실행 시작점에 묶인다 — 필터를 켜는 것만으로 없던 매수가 생기면 안 된다.

    기준가 초기화가 관문 **뒤**에 있으면 필터가 막는 동안 기준가가 비어 있다가 "필터가 처음 허락한
    봉" 의 시가로 잡힌다. 오르는 구간에서는 문턱이 함께 올라가 필터를 건 쪽이 끄면 없던 매수를 한다
    (옛 동작: 봉 4 에서 106.70 매수, 최종 자산 918.46 — 필터 없이는 체결이 아예 없는 열이다).
    필터는 "언제 살 수 있는가" 만 줄이고 "얼마에 살지" 는 건드리지 않는다 — 원칙 1·4.
    """
    closes = [100.0, 105.0, 110.0, 100.0, 98.0, 98.0, 98.0, 98.0]
    dip = {"entry_mode": "dip", "entry_dip": 3.0}

    def run(entry_filter):
        sim = make_candle_sim(_macro("E", entry_filter, dip))
        fills = []
        for i, c in enumerate(closes):
            o = closes[i - 1] if i else c
            # 고가·저가를 시가·종가 그대로 둔다(여유분 없이) — 문턱이 밀렸는지만 보는 열이다.
            fills += sim.on_candle(o, max(o, c), min(o, c), c, START + timedelta(hours=i))
        return [(f.side, round(f.price, 6), round(f.qty, 8)) for f in fills], round(sim.equity(closes[-1]), 2)

    bare_fills, bare_equity = run(None)
    filtered_fills, filtered_equity = run(MA3_ABOVE)
    assert bare_fills == [] and bare_equity == 1000.00        # 필터 없이는 문턱(97)에 닿지 않는다
    assert filtered_fills == bare_fills, filtered_fills
    assert filtered_equity == bare_equity, filtered_equity


def test_filter_does_not_block_exits():
    """관문이 닫힌 봉에서도 손절은 나간다 — 필터는 진입만 줄이고 청산은 건드리지 않는다(원칙 1)."""
    macro = Macro(**{**BASE, "rule_type": "E", "params": RULES["E"],
                     "risk": {"invest_ratio": 1.0, "stop_loss_pct": 2.0},
                     "entry_filter": MA3_ABOVE})
    # 오르는 구간에서 진입(관문 열림) -> 급락 구간에서 관문은 닫히지만 손절은 나가야 한다.
    closes = [100.0 + i * 5 for i in range(10)] + [100.0 - i * 5 for i in range(10)]
    gate = _ma_gate(closes)
    rows = _run_indexed(macro, closes)

    entries = _gated_entries(rows)
    assert entries and all(gate[i] for i, _ in entries), entries   # 진입은 관문이 열린 봉에서만
    # 핵심: 관문이 닫힌 봉의 청산이 **실제로** 있다. "매도가 하나라도 있다" 로는 관문이 열린 동안의
    # 트레일링 청산이 먼저 단정을 만족시켜, 청산까지 막는 구현이 그대로 통과한다.
    stops_while_blocked = [(i, f) for i, f in rows
                           if f.side == "sell" and not gate[i] and f.reason == "손절"]
    assert [i for i, _ in stops_while_blocked] == [11], rows
    assert round(stops_while_blocked[0][1].price, 2) == 98.00


def test_filter_does_not_block_safety_orders():
    """관문이 닫힌 봉에서도 H 세이프티오더는 나간다 — 이미 잡은 포지션을 정리하는 주문이다(원칙 5).

    익절 문턱(take_profit)을 크게 둬서 "익절 뒤 기본 주문" 순환을 막는다. 그 순환이 돌면 모든 매수가
    기본 주문이 되어 세이프티오더를 한 건도 돌리지 않고도 "매수가 둘 이상" 이 만족된다.
    """
    macro = _macro("H", MA3_ABOVE, {"price_deviation": 2.0, "take_profit": 50.0})
    closes = [100.0, 105.0, 110.0, 115.0, 112.0, 109.0, 106.0, 103.0, 100.0, 97.0]
    gate = _ma_gate(closes)
    rows = _run_indexed(macro, closes)

    base_orders = _gated_entries(rows)
    assert [i for i, _ in base_orders] == [3], rows               # 기본 주문은 관문이 열린 봉에 한 번
    # 세이프티오더는 qty_before > 0 인 매수 — 관문이 닫힌 봉(5)에서 실제로 체결되어야 한다.
    safety = [(i, f) for i, f in rows if f.side == "buy" and f.qty_before > 0.0]
    assert [i for i, _ in safety] == [4, 5], rows
    assert not gate[5], gate                                       # 봉 5 는 관문이 닫혀 있다
    assert round(safety[-1][1].price, 2) == 110.40


def test_filter_does_not_block_the_defensive_short_leg():
    """관문이 닫힌 봉에서도 K 방어 전환의 숏 다리는 나간다 — 롱 손실을 덮는 주문이다(원칙 5).

    차가운 필터로는 K 가 롱에 들어가지 못해 방어 전환 자체가 일어나지 않는다. 그래서
    "들어간 뒤 관문이 닫히는" 열이 따로 필요하다.
    """
    macro = _macro("K", MA3_ABOVE, {"partial_exit_pct": 50, "flip_to_short": True})
    closes = [100.0, 105.0, 110.0, 115.0, 114.0, 113.0, 100.0, 99.0, 98.0, 97.0]
    gate = _ma_gate(closes)
    rows = _run_indexed(macro, closes)

    assert [i for i, _ in _gated_entries(rows)] == [3], rows       # 롱 진입은 관문이 열린 봉에
    shorts = [(i, f) for i, f in rows if f.side == "short"]
    assert [i for i, _ in shorts] == [6], rows
    assert not gate[6], gate                                       # 봉 6 은 관문이 닫혀 있다
    # 같은 봉의 부분청산·청산도 함께 나간다 — 방어 전환은 세 다리가 한 묶음이다.
    assert [f.side for i, f in rows if i == 6] == ["sell", "sell", "short"], rows


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


def test_filter_judges_with_the_previous_closed_bar_not_this_one():
    """필터는 '직전 마감봉' 으로 판정한다 — 진행 중인 봉으로 자기 자신을 판정하면 미래 참조다.

    3봉 이평 '위' 필터에 100·100·100·200·200 을 준다.
      - 봉 4 종가 200 에서 비로소 200 > 평균(133.3) 이 되어 필터가 참이 된다
      - 그러므로 가장 빠른 진입은 **봉 5** 다. 봉 4 의 진입 판단은 봉 3 기준(100 > 100 거짓)
    on_candle 이 _strategy '뒤' 가 아니라 '앞' 에서 필터를 갱신하면 봉 4 에 들어가고,
    백테스트가 실거래로 재현할 수 없는 숫자를 내기 시작한다.
    """
    macro = Macro(**{**BASE, "rule_type": "E", "params": RULES["E"],
                     "entry_filter": MA3_ABOVE})
    sim = make_candle_sim(macro)
    closes = [100.0, 100.0, 100.0, 200.0, 200.0]
    first_buy = None
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        for f in sim.on_candle(o, max(o, c) * 1.01, min(o, c) * 0.99, c, START + timedelta(hours=i)):
            if f.side == "buy" and first_buy is None:
                first_buy = i
    assert first_buy == 4, f"봉 {None if first_buy is None else first_buy + 1} 에 들어갔다 — 봉 5 여야 한다"
