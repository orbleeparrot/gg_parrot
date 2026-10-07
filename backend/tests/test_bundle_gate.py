"""묶음 관문 — 한도에 닿으면 새 진입을 막고, 청산은 건드리지 않는다."""
from datetime import datetime, timezone

from app.engine.bundle import BundleGate
from app.engine.candles import LiveCandleSim, make_candle_sim
from app.engine.schema import BundleRisk, Macro


class _Sim:
    """심의 관문이 쓰는 면만 흉내낸다 — 수량 · 평균 진입가 · 투입 자본."""

    def __init__(self, qty=0.0, entry=0.0):
        self.qty, self.entry = qty, entry

    def in_position(self):
        return self.qty > 0

    def total_qty(self):
        return self.qty

    def avg_entry(self):
        return self.entry

    def committed_margin(self):
        """1배 등가 — 실제 심은 margin 을 들고 있고 1배에서 수량 × 진입가와 같다."""
        return self.qty * self.entry


def gate(total=1000.0, **limits):
    g = BundleGate(BundleRisk(**limits), total)
    return g


# --- 동시 보유 상한 ---------------------------------------------------
def test_max_positions_blocks_a_flat_leg_when_cap_reached():
    g = gate(max_positions=1)
    held, flat = _Sim(qty=1.0, entry=100.0), _Sim()
    g.register(held); g.register(flat)
    assert g.blocks(flat) is True


def test_max_positions_lets_a_leg_that_already_holds_add_more():
    """추가 매수는 새 종목이 아니다 — 막으면 마틴게일 · 분할 진입이 반 토막 난다."""
    g = gate(max_positions=1)
    held, flat = _Sim(qty=1.0, entry=100.0), _Sim()
    g.register(held); g.register(flat)
    assert g.blocks(held) is False


def test_max_positions_passes_below_cap():
    g = gate(max_positions=2)
    a, b, c = _Sim(qty=1.0, entry=100.0), _Sim(), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(b) is False


def test_max_positions_counts_only_registered_legs():
    g = gate(max_positions=1)
    flat = _Sim()
    g.register(flat)
    assert g.blocks(flat) is False          # 아무도 안 들고 있다


# --- 총 노출 한도 -----------------------------------------------------
def test_exposure_blocks_at_or_above_limit():
    """600 = 1000 × 60% — '닿으면 멈춘다'(원칙 7)이므로 같을 때도 막는다."""
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a, b = _Sim(qty=6.0, entry=100.0), _Sim()
    g.register(a); g.register(b)
    assert g.blocks(b) is True


def test_exposure_passes_below_limit():
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a, b = _Sim(qty=5.0, entry=100.0), _Sim()      # 500 < 600
    g.register(a); g.register(b)
    assert g.blocks(b) is False


def test_exposure_sums_every_leg():
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a, b, c = _Sim(qty=3.0, entry=100.0), _Sim(qty=3.0, entry=100.0), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(c) is True                     # 300 + 300 = 600


def test_exposure_blocks_a_holding_leg_too():
    """노출 한도는 '추가 매수' 도 막는다 — 보유 종목 수와 달리 금액은 더 늘어난다."""
    g = gate(total=1000.0, max_exposure_pct=60.0)
    a = _Sim(qty=6.0, entry=100.0)
    g.register(a)
    assert g.blocks(a) is True


# --- 두 한도 같이 -----------------------------------------------------
def test_both_limits_either_one_blocks():
    g = gate(total=1000.0, max_positions=2, max_exposure_pct=90.0)
    a, b, c = _Sim(qty=1.0, entry=100.0), _Sim(qty=1.0, entry=100.0), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(c) is True                     # 보유 2종목 == 상한
    g2 = gate(total=1000.0, max_positions=3, max_exposure_pct=15.0)
    for s in (a, b, c):
        g2.register(s)
    assert g2.blocks(c) is True                    # 노출 200 >= 150


def test_both_limits_pass_when_neither_binds():
    g = gate(total=1000.0, max_positions=3, max_exposure_pct=90.0)
    a, b, c = _Sim(qty=1.0, entry=100.0), _Sim(), _Sim()
    for s in (a, b, c):
        g.register(s)
    assert g.blocks(b) is False


# --- 문구 -------------------------------------------------------------
def test_note_text():
    assert gate(max_positions=3).note() == "한 번에 3종목까지"
    assert gate(max_exposure_pct=60.0).note() == "총 노출 60% 까지"
    assert gate(max_positions=3, max_exposure_pct=60.0).note() == \
        "한 번에 3종목까지 · 총 노출 60% 까지"
    assert gate(max_exposure_pct=62.5).note() == "총 노출 62.5% 까지"


# --- LiveCandleSim 벗기기 ---------------------------------------------
def test_register_unwraps_live_sim():
    """실시간은 LiveCandleSim 으로 감싼 심을 준다 — 래퍼에는 total_qty 가 없다."""
    class _Wrapper:
        def __init__(self, inner):
            self.inner = inner

    inner = _Sim(qty=6.0, entry=100.0)
    g = gate(total=1000.0, max_exposure_pct=60.0)
    g.register(_Wrapper(inner))
    flat = _Sim()
    g.register(flat)
    assert g.blocks(flat) is True
    assert g.blocks(_Wrapper(flat)) is True        # 질문할 때도 벗긴다


# --- 심에 꽂았을 때 ---------------------------------------------------
BREAKOUT = {
    "symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
    "period": {"preset": "3m"}, "params": {"k": 0.5, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0},
}


def _bars(sim, closes):
    """종가만 바꿔 가며 봉을 먹인다. 돌파 규칙이 사게 만드는 가장 단순한 모양."""
    out = []
    for i, c in enumerate(closes):
        ts = datetime(2026, 1, 1, i, tzinfo=timezone.utc)
        out.extend(sim.on_candle(c, c * 1.03, c * 0.99, c, ts, volume=1000.0))
    return out


def test_gate_none_by_default():
    assert make_candle_sim(Macro(**BREAKOUT)).bundle_gate is None


def test_gate_blocks_new_entry_in_the_sim():
    closes = [100, 101, 102, 103, 104, 105, 106]
    free = make_candle_sim(Macro(**BREAKOUT))
    baseline = _bars(free, closes)
    assert any(f.side == "buy" for f in baseline), "대조군이 안 샀다 — 시험이 무의미하다"

    blocked = make_candle_sim(Macro(**BREAKOUT))
    g = BundleGate(BundleRisk(max_positions=1), 1000.0)
    occupant = _Sim(qty=1.0, entry=100.0)      # 다른 레그가 자리를 차지했다
    g.register(occupant); g.register(blocked)
    blocked.bundle_gate = g
    assert not any(f.side == "buy" for f in _bars(blocked, closes))


def test_gate_does_not_block_the_exit():
    """Review Focus 5 — 한도에 닿아도 들고 있는 포지션은 팔 수 있어야 한다.

    원칙 1 이 깨지면 잘못 건 한도가 '못 사게' 가 아니라 '못 팔게' 가 되어 열린 손실을 키운다.
    """
    m = Macro(**{**BREAKOUT, "risk": {"invest_ratio": 1.0, "stop_loss_pct": 2.0}})
    sim = make_candle_sim(m)
    fills = _bars(sim, [100, 101, 102, 103, 104, 105, 106])
    assert sim.in_position(), "진입이 안 됐다 — 청산을 볼 수 없다"
    # 이제 한도를 꽉 채운 관문을 꽂는다. 이미 들고 있는 포지션은 손절로 나와야 한다.
    g = BundleGate(BundleRisk(max_positions=1, max_exposure_pct=1.0), 1000.0)
    g.register(_Sim(qty=100.0, entry=100.0)); g.register(sim)
    sim.bundle_gate = g
    ts = datetime(2026, 1, 2, tzinfo=timezone.utc)
    out = sim.on_candle(106, 106, 80, 82, ts, volume=1000.0)
    assert any(f.side == "sell" for f in out), "관문이 청산을 막았다 — 원칙 1 위반"
    assert not sim.in_position()


def test_live_wrapper_forwards_the_gate_to_the_inner_sim():
    """래퍼에 꽂아도 안쪽 심이 받아야 한다 — 위임이 없으면 그 레그만 조용히 한도를 무시한다.

    ``register`` 는 ``.inner`` 를 벗겨 담으므로 그 레그의 장부는 여전히 합계에 들어간다.
    그래서 다른 레그는 제대로 막히고 한도가 작동하는 듯 보인다 — 가장 찾기 어려운 모양이다.
    """
    live = LiveCandleSim(Macro(**BREAKOUT))
    g = BundleGate(BundleRisk(max_positions=1), 1000.0)
    live.bundle_gate = g
    assert live.inner.bundle_gate is g
    assert live.bundle_gate is g


def test_gate_blocks_a_live_wrapped_leg():
    """래퍼를 쓰는 실시간 레그도 실제로 막혀야 한다 — 위임이 죽으면 이 레그만 계속 들어간다."""
    closes = [100, 101, 102, 103, 104, 105, 106]

    def feed(live):
        """LiveCandleSim.on_candle 은 체결 수를 돌려준다 — Fill 목록이 아니다."""
        for i, c in enumerate(closes):
            live.on_candle(c, c * 1.03, c * 0.99, c, datetime(2026, 1, 1, i, tzinfo=timezone.utc),
                           volume=1000.0)

    free = LiveCandleSim(Macro(**BREAKOUT))
    feed(free)
    assert free.inner.in_position(), "대조군이 안 샀다 — 시험이 무의미하다"

    blocked = LiveCandleSim(Macro(**BREAKOUT))
    g = BundleGate(BundleRisk(max_positions=1), 1000.0)
    g.register(_Sim(qty=1.0, entry=100.0))      # 다른 레그가 자리를 차지했다
    g.register(blocked)
    blocked.bundle_gate = g
    feed(blocked)
    assert not blocked.inner.in_position()


# --- 노출은 투입 자본으로 센다 -----------------------------------------
def test_exposure_counts_capital_not_leveraged_notional():
    """3배로 500 을 넣으면 투입 자본은 500 이고 명목은 1500 이다. 한도는 자본을 센다 —
    아니면 한도를 100% 로 열어 둔 사용자가 레그를 잃는다."""
    m = Macro(**{**BREAKOUT, "leverage": 3, "market": "futures"})
    sim = make_candle_sim(m)
    _bars(sim, [100, 101, 102, 103, 104, 105, 106])
    assert sim.in_position()
    # 투입 자본은 초기자금 이하, 명목은 그보다 크다.
    assert sim.committed_margin() <= sim.initial_capital
    assert sim.total_qty() * sim.avg_entry() > sim.committed_margin()


def test_leveraged_leg_does_not_eat_the_whole_exposure_cap():
    """3배 레그 하나가 한도 100% 를 다 먹으면 안 된다 — 명목으로 세던 시절의 결함."""
    m = Macro(**{**BREAKOUT, "leverage": 3, "market": "futures"})
    held = make_candle_sim(m, initial_capital=500.0)
    _bars(held, [100, 101, 102, 103, 104, 105, 106])
    assert held.in_position()
    assert held.total_qty() * held.avg_entry() > 1000.0, "명목이 묶음 자금을 넘지 않으면 시험이 무의미하다"

    g = BundleGate(BundleRisk(max_exposure_pct=100.0), 1000.0)
    flat = _Sim()
    g.register(held); g.register(flat)
    assert g.blocks(flat) is False


# --- 정리성 주문은 포화된 관문에서도 나간다 (설계 원칙 5) ----------------
def _ohlc(sim, bars, hour0=0):
    """(o, h, l, c) 봉을 그대로 먹인다 — 정리성 주문은 봉 안 저가 · 고가로 판정된다."""
    out = []
    for i, (o, h, l, c) in enumerate(bars):
        ts = datetime(2026, 1, 1, hour0 + i, tzinfo=timezone.utc)
        out.extend(sim.on_candle(o, h, l, c, ts, volume=1000.0))
    return out


def _saturated_gate(sim):
    """이 심이 **새로** 들어가려 하면 반드시 막는 관문. 이미 들고 있어도 노출로 막는다."""
    g = BundleGate(BundleRisk(max_positions=1, max_exposure_pct=1.0), 1000.0)
    g.register(_Sim(qty=100.0, entry=100.0))     # 다른 레그가 자리와 노출을 다 먹었다
    g.register(sim)
    sim.bundle_gate = g
    return g


MARTINGALE = {
    "symbol": "BTCUSDT", "rule_type": "H", "candle_interval": "1h",
    "period": {"preset": "3m"},
    # 익절 50% 는 이 봉 계열에서 닿지 않는다 — 안전주문만 보려고 멀리 둔다.
    "params": {"base_order_size": 100.0, "safety_order_size": 100.0, "price_deviation": 5.0,
               "max_safety_orders": 2, "take_profit": 50.0, "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0},
}

# 1봉: 기본 주문(종가 100). 2봉: 저가 94 가 안전주문 가격 95 를 지난다.
MARTINGALE_BARS = [(100.0, 101.0, 99.0, 100.0), (100.0, 100.0, 94.0, 96.0)]

SAR = {
    "symbol": "BTCUSDT", "rule_type": "K", "candle_interval": "1h",
    "period": {"preset": "3m"}, "market": "futures",
    "params": {"drop_trigger_pct": 5.0, "partial_exit_pct": 50.0, "flip_to_short": True,
               "short_take_profit_pct": 50.0, "short_stop_loss_pct": 50.0,
               "initial_capital": 1000},
    "risk": {"invest_ratio": 1.0},
}

# 1봉: 롱 진입(종가 100). 2봉: 저가 94 가 방어 발동선 95 를 지난다 → 부분청산 + 숏 전환.
SAR_BARS = [(100.0, 101.0, 99.0, 100.0), (100.0, 100.0, 94.0, 96.0)]


def test_saturated_gate_does_not_block_a_martingale_safety_order():
    """H 안전주문은 '정리성 주문' 이다 — 한도가 포화돼도 평단을 받칠 수 있어야 한다.

    원칙 5 가 깨지면 한도가 '못 사게' 가 아니라 '못 받치게' 가 되어 열린 손실을 키운다.
    """
    # 대조군: 관문 없이는 안전주문이 나간다.
    free = make_candle_sim(Macro(**MARTINGALE))
    free_fills = _ohlc(free, MARTINGALE_BARS)
    assert len([f for f in free_fills if f.side == "buy"]) == 2, \
        f"대조군이 기본 주문 + 안전주문을 내지 않았다 — 봉 계열을 고쳐라: {free_fills}"

    # 기본 주문까지 가고 나서 관문을 포화시킨다.
    sim = make_candle_sim(Macro(**MARTINGALE))
    first = _ohlc(sim, MARTINGALE_BARS[:1])
    assert len([f for f in first if f.side == "buy"]) == 1 and sim.in_position()
    _saturated_gate(sim)
    assert sim.bundle_gate.blocks(sim) is True, "관문이 포화되지 않았다 — 시험이 무의미하다"

    rest = _ohlc(sim, MARTINGALE_BARS[1:], hour0=1)
    assert [f.side for f in rest] == ["buy"], f"관문이 안전주문을 막았다 — 원칙 5 위반: {rest}"
    assert sim.total_qty() > first[0].qty


def test_saturated_gate_does_not_block_the_sar_defense_short():
    """K 방어 숏 전환도 정리성 주문이다 — 한도가 포화돼도 롱을 받치는 숏이 나가야 한다."""
    # 대조군: 관문 없이는 부분청산 + 숏 전환이 일어난다.
    free = make_candle_sim(Macro(**SAR))
    free_fills = _ohlc(free, SAR_BARS)
    assert [f.side for f in free_fills] == ["buy", "sell", "sell", "short"], \
        f"대조군이 방어 전환을 하지 않았다 — 봉 계열을 고쳐라: {[f.side for f in free_fills]}"

    sim = make_candle_sim(Macro(**SAR))
    first = _ohlc(sim, SAR_BARS[:1])
    assert [f.side for f in first] == ["buy"] and sim.in_position()
    _saturated_gate(sim)
    assert sim.bundle_gate.blocks(sim) is True, "관문이 포화되지 않았다 — 시험이 무의미하다"

    rest = _ohlc(sim, SAR_BARS[1:], hour0=1)
    assert [f.side for f in rest] == ["sell", "sell", "short"], \
        f"관문이 방어 전환을 막았다 — 원칙 5 위반: {[f.side for f in rest]}"
    assert sim.in_position()
