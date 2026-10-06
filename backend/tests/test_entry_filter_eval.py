"""필터 평가기 — 네 종류의 판정, 그리고 '모르면 막는다'."""
from app.engine.entry_filter import make_filter
from app.engine.schema import Macro

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}


def _eval(kind, params):
    return make_filter(Macro(**{**BREAKOUT, "entry_filter": {"kind": kind, "params": params}}))


def test_no_filter_returns_none():
    assert make_filter(Macro(**BREAKOUT)) is None


def test_unwarmed_filter_blocks():
    f = _eval("ma", {"period": 20, "side": "above"})
    assert f.allows() is False            # 봉을 하나도 안 먹였다
    for close in range(10):               # 20봉이 필요한데 10봉만
        f.update(float(100 + close))
    assert f.allows() is False


def test_ma_above_allows_when_close_is_over_the_average():
    f = _eval("ma", {"period": 3, "side": "above"})
    for close in (100.0, 100.0, 100.0):
        f.update(close)
    assert f.allows() is False            # 종가 == 평균, "위" 가 아니다
    f.update(200.0)
    assert f.allows() is True


def test_ma_below_is_the_mirror():
    f = _eval("ma", {"period": 3, "side": "below"})
    for close in (100.0, 100.0, 100.0, 50.0):
        f.update(close)
    assert f.allows() is True


def test_rsi_max_blocks_when_overbought():
    f = _eval("rsi", {"period": 2, "max": 70})
    for close in (100.0, 110.0, 120.0, 130.0, 140.0):   # 계속 오름 -> RSI 100 근처
        f.update(close)
    assert f.allows() is False


def test_rsi_min_blocks_when_oversold():
    f = _eval("rsi", {"period": 2, "min": 30})
    for close in (140.0, 130.0, 120.0, 110.0, 100.0):   # 계속 내림 -> RSI 0 근처
        f.update(close)
    assert f.allows() is False


def test_bollinger_zones():
    below = _eval("bb", {"period": 3, "num_std": 1.0, "zone": "below_lower"})
    for close in (100.0, 100.0, 100.0, 50.0):
        below.update(close)
    assert below.allows() is True
    inside = _eval("bb", {"period": 3, "num_std": 1.0, "zone": "inside"})
    for close in (100.0, 101.0, 100.0):
        inside.update(close)
    assert inside.allows() is True


def test_volume_multiple():
    f = _eval("volume", {"period": 3, "multiple": 2.0})
    for close in (100.0, 100.0, 100.0):
        f.update(close, volume=10.0)
    assert f.allows() is False            # 평균과 같다
    f.update(100.0, volume=100.0)
    assert f.allows() is True


def test_volume_filter_blocks_without_volume():
    """거래량을 못 받으면 막는다 — 통과시키면 필터를 켠 사용자가 필터 없이 거래한다."""
    f = _eval("volume", {"period": 2, "multiple": 2.0})
    for _ in range(5):
        f.update(100.0)                   # volume 없음
    assert f.allows() is False


def test_note_describes_the_filter_in_korean():
    assert "20" in _eval("ma", {"period": 20, "side": "above"}).note()
    for kind, params in (("ma", {"period": 20, "side": "above"}),
                         ("rsi", {"period": 14, "max": 70}),
                         ("bb", {"period": 20, "zone": "inside"}),
                         ("volume", {"period": 20, "multiple": 2.0})):
        note = _eval(kind, params).note()
        assert note and note.strip() == note
