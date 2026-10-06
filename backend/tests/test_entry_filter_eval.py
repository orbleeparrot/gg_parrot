"""필터 평가기 — 네 종류의 판정, 그리고 '모르면 막는다'.

기대값은 구현 출력을 베낀 것이 아니라 손으로 계산한 값이다. 계산 근거는 각 테스트 위 주석에 있다.
"""
from app.engine.entry_filter import make_filter
from app.engine.schema import Macro

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
            "params": {"k": 0.5, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0}}


def _eval(kind, params):
    return make_filter(Macro(**{**BREAKOUT, "entry_filter": {"kind": kind, "params": params}}))


def _feed(f, closes, volumes=None):
    for i, close in enumerate(closes):
        f.update(close, None if volumes is None else volumes[i])


def _volume_filter(period, multiple, volumes):
    f = _eval("volume", {"period": period, "multiple": multiple})
    for v in volumes:
        f.update(100.0, volume=v)
    return f


def test_no_filter_returns_none():
    assert make_filter(Macro(**BREAKOUT)) is None


# --- 워밍업: 지표 값을 아직 모르면 네 종류 모두 막는다 -------------------------
def test_unwarmed_filter_blocks():
    f = _eval("ma", {"period": 20, "side": "above"})
    assert f.allows() is False            # 봉을 하나도 안 먹였다
    for close in range(10):               # 20봉이 필요한데 10봉만
        f.update(float(100 + close))
    assert f.allows() is False


def test_rsi_warmup_blocks_until_period_plus_one_closes():
    # min=0 은 RSI 가 값만 있으면 항상 통과한다 -> 막히는 이유는 워밍업뿐.
    # RSI(3) 은 첫 종가가 기준점이라 종가 4개(변화 3개)가 있어야 값이 나온다.
    f = _eval("rsi", {"period": 3, "min": 0})
    assert f.allows() is False
    for close in (100.0, 110.0, 120.0):
        f.update(close)
        assert f.allows() is False        # 3개 먹였다 -> 아직
    f.update(130.0)
    assert f.allows() is True             # 4개째에 값이 생기고 통과


def test_bollinger_warmup_blocks_until_period_closes():
    # 마지막 종가 100 은 창(100,110,100) 밴드 안 -> 막히는 이유는 워밍업뿐.
    f = _eval("bb", {"period": 3, "num_std": 1.0, "zone": "inside"})
    assert f.allows() is False
    f.update(100.0)
    assert f.allows() is False
    f.update(110.0)
    assert f.allows() is False            # 2개 먹였다 -> 아직
    f.update(100.0)
    assert f.allows() is True             # 3개째에 창이 차고 통과


def test_volume_warmup_blocks_until_preceding_window_is_full():
    # 기준은 '직전 period 봉' 이라 period 개를 먹인 봉까지는 기준이 없다.
    f = _eval("volume", {"period": 3, "multiple": 2.0})
    assert f.allows() is False
    for _ in range(3):
        f.update(100.0, volume=1000.0)    # 거래량이 아무리 커도 기준이 없으면 막는다
        assert f.allows() is False
    f.update(100.0, volume=1000.0)        # 직전 3봉 평균 1000, 1000 >= 2000 아님
    assert f.allows() is False
    f.update(100.0, volume=2000.0)        # 직전 3봉 평균 1000, 2000 >= 2000
    assert f.allows() is True


# --- 이동평균 -------------------------------------------------------------
def test_ma_above_allows_when_close_is_over_the_average():
    f = _eval("ma", {"period": 3, "side": "above"})
    for close in (100.0, 100.0, 100.0):
        f.update(close)
    assert f.allows() is False            # 종가 == 평균, "위" 가 아니다
    f.update(200.0)
    assert f.allows() is True


def test_ma_below_is_the_mirror():
    f = _eval("ma", {"period": 3, "side": "below"})
    for close in (100.0, 100.0, 100.0):
        f.update(close)
    assert f.allows() is False            # 종가 == 평균, "아래" 도 아니다
    f.update(50.0)
    assert f.allows() is True


# EMA(3): k = 2/(3+1) = 0.5, 첫 값은 앞 3개 종가의 SMA.
#   종가 10,10,10 -> 10
#   40 -> 0.5*40 + 0.5*10 = 25
#   40 -> 0.5*40 + 0.5*25 = 32.5
#   36 -> 0.5*36 + 0.5*32.5 = 34.25      마지막 종가 36 > 34.25  -> "위"
# 같은 종가의 SMA(3) = (40+40+36)/3 = 38.667                  36 < 38.667 -> "위" 아님
def test_ma_ema_type_is_used_for_above():
    closes = (10.0, 10.0, 10.0, 40.0, 40.0, 36.0)
    ema = _eval("ma", {"ma_type": "EMA", "period": 3, "side": "above"})
    _feed(ema, closes)
    assert ema.allows() is True
    sma = _eval("ma", {"ma_type": "SMA", "period": 3, "side": "above"})
    _feed(sma, closes)
    assert sma.allows() is False


# 거울 시리즈. EMA(3): 40,40,40 -> 40 / 10 -> 25 / 10 -> 17.5 / 14 -> 0.5*14 + 0.5*17.5 = 15.75
#   마지막 종가 14 < 15.75 -> "아래".   SMA(3) = (10+10+14)/3 = 11.333, 14 < 11.333 아님.
def test_ma_ema_type_is_used_for_below():
    closes = (40.0, 40.0, 40.0, 10.0, 10.0, 14.0)
    ema = _eval("ma", {"ma_type": "EMA", "period": 3, "side": "below"})
    _feed(ema, closes)
    assert ema.allows() is True
    sma = _eval("ma", {"ma_type": "SMA", "period": 3, "side": "below"})
    _feed(sma, closes)
    assert sma.allows() is False


# --- RSI ------------------------------------------------------------------
# RSI(2) 는 Wilder: 첫 종가는 기준점, 변화 2개의 평균 상승/하락으로 첫 값을 낸다.
#   100,110,120 : 변화 +10,+10 -> 평균 상승 10, 평균 하락 0 -> 하락이 0 이면 100.0
#   120,110,100 : 변화 -10,-10 -> 평균 상승 0, 평균 하락 10 -> RS = 0 -> 100 - 100/1 = 0.0
#   100,110,100 : 변화 +10,-10 -> 평균 상승 5, 평균 하락 5 -> RS = 1 -> 100 - 100/2 = 50.0
#   100,110,105 : 변화 +10,-5  -> 평균 상승 5, 평균 하락 2.5 -> RS = 2 -> 100 - 100/3 = 66.667
RISING = (100.0, 110.0, 120.0)        # RSI 100
FALLING = (120.0, 110.0, 100.0)       # RSI 0
FLAT_50 = (100.0, 110.0, 100.0)       # RSI 50
MILD_UP = (100.0, 110.0, 105.0)       # RSI 66.667


def _rsi(closes, **bounds):
    f = _eval("rsi", {"period": 2, **bounds})
    _feed(f, closes)
    return f


def test_rsi_max_blocks_when_overbought():
    f = _eval("rsi", {"period": 2, "max": 70})
    for close in (100.0, 110.0, 120.0, 130.0, 140.0):   # 계속 오름 -> RSI 100
        f.update(close)
    assert f.allows() is False


def test_rsi_min_blocks_when_oversold():
    f = _eval("rsi", {"period": 2, "min": 30})
    for close in (140.0, 130.0, 120.0, 110.0, 100.0):   # 계속 내림 -> RSI 0
        f.update(close)
    assert f.allows() is False


def test_rsi_allows_when_inside_the_band():
    assert _rsi(FLAT_50, min=30, max=70).allows() is True       # 50 은 30~70 안
    assert _rsi(FLAT_50, min=30).allows() is True               # 아래쪽 한계만
    assert _rsi(FLAT_50, max=70).allows() is True               # 위쪽 한계만
    # 반대편이 열려 있으면 극단값도 통과한다(항상 막는 구현을 잡는다)
    assert _rsi(RISING, min=30).allows() is True                # RSI 100 >= 30
    assert _rsi(FALLING, max=70).allows() is True               # RSI 0 <= 70


def test_rsi_min_is_inclusive():
    assert _rsi(FLAT_50, min=50).allows() is True               # 50 >= 50
    assert _rsi(RISING, min=100).allows() is True               # 100 >= 100
    assert _rsi(FLAT_50, min=51).allows() is False              # 50 < 51
    assert _rsi(FLAT_50, min=50, max=70).allows() is True       # 둘 다 있어도 min 경계는 포함


def test_rsi_max_is_inclusive():
    assert _rsi(FLAT_50, max=50).allows() is True               # 50 <= 50
    assert _rsi(FALLING, max=0).allows() is True                # 0 <= 0
    assert _rsi(FLAT_50, max=49).allows() is False              # 50 > 49
    assert _rsi(FLAT_50, min=30, max=50).allows() is True       # 둘 다 있어도 max 경계는 포함


def test_rsi_both_bounds_bracket_the_value():
    assert _rsi(MILD_UP, min=66, max=67).allows() is True       # 66.667 은 66~67 안
    assert _rsi(MILD_UP, min=67, max=80).allows() is False      # 66.667 < 67
    assert _rsi(MILD_UP, min=40, max=66).allows() is False      # 66.667 > 66
    assert _rsi(FLAT_50, min=50, max=50).allows() is True       # 한 점 구간도 양쪽 포함


# --- 볼린저 ---------------------------------------------------------------
# 모집단 표준편차, 창 3, num_std 1.0. 밴드는 마지막 종가를 포함한 창에서 계산한다.
#   아래로 이탈: 창 (100,100,50)  평균 250/3 = 83.333, 편차 +16.667,+16.667,-33.333
#                분산 = (277.78+277.78+1111.11)/3 = 555.56, sd = 23.570
#                상단 106.904, 하단 59.763 -> 종가 50 < 하단
#   위로 이탈:   창 (100,100,150) 평균 116.667, 분산 555.56, sd = 23.570
#                상단 140.237, 하단 93.097 -> 종가 150 > 상단
#   밴드 안:     창 (100,110,100) 평균 103.333, 분산 (11.11+44.44+11.11)/3 = 22.22, sd = 4.714
#                상단 108.047, 하단 98.619 -> 종가 100 은 둘 사이
BELOW_BAND = (100.0, 100.0, 100.0, 50.0)
ABOVE_BAND = (100.0, 100.0, 100.0, 150.0)
IN_BAND = (100.0, 110.0, 100.0)


def _bb(zone, closes, period=3, num_std=1.0):
    f = _eval("bb", {"period": period, "num_std": num_std, "zone": zone})
    _feed(f, closes)
    return f


def test_bollinger_zones():
    assert _bb("below_lower", BELOW_BAND).allows() is True
    assert _bb("inside", IN_BAND).allows() is True


def test_bollinger_above_upper_passes_when_close_is_over_the_upper_band():
    assert _bb("above_upper", ABOVE_BAND).allows() is True


def test_bollinger_each_zone_only_accepts_its_own_position():
    # 종가가 아래 / 안 / 위 에 있을 때 각 구역 필터의 답.
    # 안쪽 종가는 '아래 이탈' 도 '위 이탈' 도 아니다 -> 상단·하단을 바꿔 쓴 구현을 잡는다.
    assert _bb("below_lower", IN_BAND).allows() is False        # 100 < 98.619 아님 (상단과 비교하면 True)
    assert _bb("above_upper", IN_BAND).allows() is False        # 100 > 108.047 아님 (하단과 비교하면 True)
    assert _bb("above_upper", BELOW_BAND).allows() is False     # 50 은 위가 아니다
    assert _bb("below_lower", ABOVE_BAND).allows() is False     # 150 은 아래가 아니다
    assert _bb("inside", BELOW_BAND).allows() is False
    assert _bb("inside", ABOVE_BAND).allows() is False


# 창 2, num_std 1.0 이면 밴드가 창의 최저·최고가와 정확히 겹친다:
#   창 (100,110) 평균 105, 분산 (25+25)/2 = 25, sd 5 -> 상단 110, 하단 100
def test_bollinger_band_edge_is_inside_not_outside():
    on_upper = (100.0, 110.0)             # 종가 110 == 상단
    assert _bb("inside", on_upper, period=2).allows() is True
    assert _bb("above_upper", on_upper, period=2).allows() is False
    on_lower = (110.0, 100.0)             # 종가 100 == 하단
    assert _bb("inside", on_lower, period=2).allows() is True
    assert _bb("below_lower", on_lower, period=2).allows() is False


# --- 거래량 ---------------------------------------------------------------
# 기준 = 직전 period 봉의 평균(이 봉 제외).
def test_volume_multiple():
    f = _eval("volume", {"period": 3, "multiple": 2.0})
    for close in (100.0, 100.0, 100.0):
        f.update(close, volume=10.0)
    assert f.allows() is False            # 직전 창이 아직 안 찼다
    f.update(100.0, volume=100.0)         # 직전 3봉 평균 10, 100 >= 20
    assert f.allows() is True


def test_volume_threshold_is_inclusive():
    # 직전 3봉 평균 10, multiple 2 -> 문턱 20
    assert _volume_filter(3, 2.0, (10.0, 10.0, 10.0, 20.0)).allows() is True    # 정확히 2배
    assert _volume_filter(3, 2.0, (10.0, 10.0, 10.0, 19.99)).allows() is False  # 2배 미만


def test_volume_baseline_excludes_the_current_bar():
    # multiple(5) > period(3) 인 설정도 거래량이 충분히 크면 통과해야 한다.
    # 이 봉을 기준에 넣으면 (10+10+V)/3*5 <= V 는 V <= -50 이라 영원히 불가능하다.
    assert _volume_filter(3, 5.0, (10.0, 10.0, 10.0, 1000.0)).allows() is True
    assert _volume_filter(3, 5.0, (10.0, 10.0, 10.0, 50.0)).allows() is True    # 정확히 5배
    assert _volume_filter(3, 5.0, (10.0, 10.0, 10.0, 49.0)).allows() is False


def test_volume_baseline_slides_over_the_preceding_bars():
    # 10,10,10,100 이 지나면 다음 봉의 기준은 (10,10,100) 평균 40 -> 문턱 80.
    assert _volume_filter(3, 2.0, (10.0, 10.0, 10.0, 100.0, 80.0)).allows() is True
    assert _volume_filter(3, 2.0, (10.0, 10.0, 10.0, 100.0, 79.0)).allows() is False


def test_volume_zero_baseline_blocks():
    # 거래가 없었다는 건 급증의 반대다. 0 >= 0*multiple 로 통과하면 안 된다.
    assert _volume_filter(2, 2.0, (0.0, 0.0, 0.0)).allows() is False
    assert _volume_filter(2, 2.0, (0.0, 0.0, 5.0)).allows() is False


def test_volume_filter_blocks_without_volume():
    """거래량을 못 받으면 막는다 — 통과시키면 필터를 켠 사용자가 필터 없이 거래한다."""
    f = _eval("volume", {"period": 2, "multiple": 2.0})
    for _ in range(5):
        f.update(100.0)                   # volume 없음
    assert f.allows() is False


def test_volume_unknown_bar_blocks_and_is_not_part_of_the_baseline():
    # 기준이 찬 뒤 거래량을 모르는 봉이 오면 그 봉은 막고, 창에는 넣지 않는다.
    f = _volume_filter(2, 2.0, (10.0, 10.0))
    f.update(100.0, volume=None)
    assert f.allows() is False
    f.update(100.0, volume=19.0)          # 기준은 여전히 (10,10) 평균 10 -> 문턱 20
    assert f.allows() is False            # None 을 0 으로 넣었다면 기준 5 라 통과해버린다
    g = _volume_filter(2, 2.0, (10.0, 10.0))
    g.update(100.0, volume=None)
    g.update(100.0, volume=20.0)
    assert g.allows() is True             # None 이 창을 비우거나 밀었다면 여기서 막힌다


# --- note -----------------------------------------------------------------
def test_note_ma_text():
    assert _eval("ma", {"period": 20, "side": "above"}).note() == "20봉 SMA 이동평균 위"
    assert _eval("ma", {"period": 20, "side": "below"}).note() == "20봉 SMA 이동평균 아래"
    assert _eval("ma", {"ma_type": "EMA", "period": 50, "side": "above"}).note() == "50봉 EMA 이동평균 위"


def test_note_rsi_text():
    assert _eval("rsi", {"period": 14, "max": 70}).note() == "RSI(14) 70 이하"
    assert _eval("rsi", {"period": 14, "min": 30}).note() == "RSI(14) 30 이상"
    assert _eval("rsi", {"period": 14, "min": 30, "max": 70}).note() == "RSI(14) 30~70"


def test_note_bollinger_text():
    assert _eval("bb", {"period": 20, "zone": "below_lower"}).note() == "볼린저(20, 2σ) 하단 밖"
    assert _eval("bb", {"period": 20, "zone": "above_upper"}).note() == "볼린저(20, 2σ) 상단 밖"
    assert _eval("bb", {"period": 20, "num_std": 1.5, "zone": "inside"}).note() == "볼린저(20, 1.5σ) 밴드 안"


def test_note_volume_text():
    assert _eval("volume", {"period": 20, "multiple": 2.0}).note() == "거래량이 20봉 평균의 2배 이상"
    assert _eval("volume", {"period": 10, "multiple": 2.5}).note() == "거래량이 10봉 평균의 2.5배 이상"
