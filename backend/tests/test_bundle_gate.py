"""묶음 관문 — 한도에 닿으면 새 진입을 막고, 청산은 건드리지 않는다."""
import pytest

from app.engine.bundle import BundleGate
from app.engine.schema import BundleRisk


class _Sim:
    """심의 관문이 쓰는 면만 흉내낸다 — 수량과 평균 진입가."""

    def __init__(self, qty=0.0, entry=0.0):
        self.qty, self.entry = qty, entry

    def in_position(self):
        return self.qty > 0

    def total_qty(self):
        return self.qty

    def avg_entry(self):
        return self.entry


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
from datetime import datetime, timezone

from app.engine.candles import make_candle_sim
from app.engine.schema import Macro

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
