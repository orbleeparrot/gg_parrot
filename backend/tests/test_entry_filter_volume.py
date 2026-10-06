"""거래량 필터 — 봉의 거래량이 심까지 와야 작동하고, 안 오면 막는다."""
from datetime import datetime, timedelta, timezone

from app.engine.candles import LiveCandleSim, make_candle_sim
from app.engine.driver import Leg, StrategyDriver
from app.engine.schema import Macro

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
VOL2X = {"kind": "volume", "params": {"period": 3, "multiple": 2.0}}
BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.1, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
        "fees": {"commission_pct": 0, "slippage_pct": 0}}


def test_on_candle_accepts_volume_and_the_filter_uses_it():
    sim = make_candle_sim(Macro(**{**BODY, "entry_filter": VOL2X}))
    closes = [100.0 + i * 5 for i in range(12)]
    volumes = [10.0] * 8 + [500.0] * 4          # 뒤에서 거래량이 터진다
    fills = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        fills += sim.on_candle(o, c * 1.02, o * 0.98, c, START + timedelta(hours=i), volume=volumes[i])
    assert [f for f in fills if f.side == "buy"]


def test_without_volume_the_filter_blocks_everything():
    sim = make_candle_sim(Macro(**{**BODY, "entry_filter": VOL2X}))
    closes = [100.0 + i * 5 for i in range(12)]
    fills = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        fills += sim.on_candle(o, c * 1.02, o * 0.98, c, START + timedelta(hours=i))
    assert not [f for f in fills if f.side == "buy"]


def test_warmup_passes_volume_when_the_candle_carries_it():
    sim = make_candle_sim(Macro(**{**BODY, "entry_filter": VOL2X}))
    # (t_ms, o, h, l, c, v) 6칸 봉
    candles = [(START.timestamp() * 1000 + i * 3_600_000, 100.0, 101.0, 99.0, 100.0, 10.0)
               for i in range(5)]
    sim.warmup(candles)
    assert sim.entry_filter.allows() is False   # 평균과 같으니 2배가 아니다


def test_warmup_still_accepts_five_column_candles():
    """거래량 없는 옛 봉 모양도 그대로 받는다."""
    sim = make_candle_sim(Macro(**BODY))
    candles = [(START.timestamp() * 1000 + i * 3_600_000, 100.0, 101.0, 99.0, 100.0)
               for i in range(5)]
    sim.warmup(candles)   # 예외가 없으면 통과


def test_push_candle_accepts_both_candle_shapes():
    macro = Macro(**{**BODY, "entry_filter": VOL2X})
    driver = StrategyDriver([Leg("BTCUSDT", LiveCandleSim(macro, initial_capital=1000.0), 1000.0)], 1000.0, macro=macro)
    t = int(START.timestamp() * 1000)
    assert driver.push_candle("BTCUSDT", (t, 100.0, 101.0, 99.0, 100.0)) == 0
    assert driver.push_candle("BTCUSDT", (t + 3_600_000, 100.0, 101.0, 99.0, 100.0, 10.0)) == 0
