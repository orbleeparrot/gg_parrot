"""마감봉 피드가 거래량을 싣는다 — 라이브·페이퍼에서 거래량 필터가 백테스트처럼 작동해야 한다.

원시 봉 dict 의 ``"v"`` 가 피드의 Candle 을 거쳐 심까지 가야 한다. 피드가 이걸 떨어뜨리면 거래량 필터는
라이브에서 영원히 막는다(백테스트에서는 되는데 — 원칙 4 위반, 오류도 없다).
"""
import asyncio
from datetime import datetime, timezone

from app.engine.candle_feed import Candle, CandleFeed, _to_candle
from app.engine.candles import LiveCandleSim, make_candle_sim
from app.engine.driver import Leg, StrategyDriver
from app.engine.schema import Macro

START_MS = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
HOUR = 3_600_000
VOL2X = {"kind": "volume", "params": {"period": 3, "multiple": 2.0}}
BODY = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h", "period": {"preset": "3m"},
        "params": {"k": 0.1, "initial_capital": 1000}, "risk": {"invest_ratio": 1.0},
        "fees": {"commission_pct": 0, "slippage_pct": 0}, "entry_filter": VOL2X}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _row(i, v="absent", closed=True):
    """바이낸스/업비트 로더가 내는 원시 봉 모양. ``v="absent"`` 면 거래량 키 자체가 없다."""
    row = {"t": START_MS + i * HOUR, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "closed": closed}
    if v != "absent":
        row["v"] = v
    return row


def _feed_serving(rows):
    async def fetch(symbol, interval, limit, market):
        return rows

    return CandleFeed(fetch=fetch, now_ms=lambda: START_MS + 100 * HOUR, sleep=lambda s: asyncio.sleep(0))


def _sim():
    return make_candle_sim(Macro(**BODY))


# -- _to_candle ------------------------------------------------------------
def test_to_candle_carries_volume_through():
    c = _to_candle({**_row(0, 12.5)})
    assert c.v == 12.5 and c[5] == 12.5          # 심은 인덱스 5 로 읽는다
    assert _to_candle(_row(0, "7.25")).v == 7.25  # 문자열 숫자도 float 로
    assert _to_candle(_row(0, 0)).v == 0.0        # 진짜 0 은 0 이다 — None 으로 뭉개지 않는다


def test_to_candle_leaves_volume_none_when_unknown():
    assert _to_candle(_row(0)).v is None          # 키 없음
    assert _to_candle(_row(0, None)).v is None    # 값 None
    assert _to_candle(_row(0, "n/a")).v is None   # 숫자 아님
    assert _to_candle(_row(0, float("nan"))).v is None


def test_five_argument_candle_still_constructs():
    assert Candle(1, 2.0, 3.0, 1.0, 2.5).v is None


# -- 피드 경로 ---------------------------------------------------------------
def test_history_rows_carry_volume():
    rows = [_row(0, 10.0), _row(1, 20.0), _row(2, 30.0), _row(3, 99.0, closed=False)]
    got = _run(_feed_serving(rows).history("BTCUSDT", "1h", "spot", 3))
    assert [c.v for c in got] == [10.0, 20.0, 30.0]


def test_subscribed_callback_receives_candles_carrying_volume():
    rows = [_row(0, 11.0), _row(1, 22.0), _row(2, 33.0, closed=False)]
    feed = _feed_serving(rows)
    got = []

    async def cb(symbol, candle):
        got.append(candle)

    feed.subscribe("BTCUSDT", "1h", "spot", cb, since_t=START_MS - HOUR)
    _run(feed.poll_once(("BTCUSDT", "1h", "spot")))
    assert [c.v for c in got] == [11.0, 22.0]


# -- 끝에서 끝까지: 이 수정의 목적 -----------------------------------------------
def test_volume_filter_warms_up_from_feed_history_and_passes_on_a_spike():
    """피드가 내놓은 history 로 웜업한 심이 거래량 급증봉에서 True 판정을 낸다.

    피드가 거래량을 떨어뜨리면 필터는 영원히 False 라서 이 시험이 빨개진다.
    """
    rows = [_row(0, 10.0), _row(1, 10.0), _row(2, 10.0), _row(3, 500.0), _row(4, 1.0, closed=False)]
    history = _run(_feed_serving(rows).history("BTCUSDT", "1h", "spot", 4))
    sim = _sim()
    sim.warmup(history)                      # paper.py / runner_engine.py 가 하는 그대로
    assert sim.entry_filter.allows() is True


def test_volume_filter_does_not_pass_without_a_spike():
    rows = [_row(i, 10.0) for i in range(5)] + [_row(5, 1.0, closed=False)]
    history = _run(_feed_serving(rows).history("BTCUSDT", "1h", "spot", 5))
    sim = _sim()
    sim.warmup(history)
    assert sim.entry_filter.allows() is False   # 평균과 같다 — 2배가 아니다


def test_volume_filter_reaches_true_on_a_live_candle_delivered_by_the_feed():
    """웜업 뒤 라이브로 한 봉씩 들어오는 길(구독 콜백 -> 드라이버 -> 심)도 거래량을 싣는다."""
    macro = Macro(**BODY)
    sim = LiveCandleSim(macro, initial_capital=1000.0)
    driver = StrategyDriver([Leg("BTCUSDT", sim, 1000.0)], 1000.0, macro=macro)

    warm_rows = [_row(i, 10.0) for i in range(3)]
    live_rows = warm_rows + [_row(3, 500.0), _row(4, 1.0, closed=False)]
    warm = _run(_feed_serving(warm_rows + [_row(3, 0.0, closed=False)]).history("BTCUSDT", "1h", "spot", 3))
    sim.warmup(warm)
    assert sim.inner.entry_filter.allows() is False   # 창은 찼지만 급증이 아직 없다

    feed = _feed_serving(live_rows)

    async def cb(symbol, candle):
        driver.push_candle(symbol, candle)

    feed.subscribe("BTCUSDT", "1h", "spot", cb, since_t=warm[-1].t)
    _run(feed.poll_once(("BTCUSDT", "1h", "spot")))
    assert sim.inner.entry_filter.allows() is True


def test_missing_volume_is_unknown_not_zero_so_it_cannot_fake_a_spike():
    """거래량을 모르는 봉이 0.0 으로 둔갑해 창에 들어가면 기준이 깎여 15 가 '2배' 로 통과한다.
    None 이면 창에서 빠져 기준은 10 그대로고, 15 는 2배(20)가 못 된다."""
    rows = [_row(0, 10.0), _row(1, 10.0), _row(2, 10.0), _row(3), _row(4, 15.0), _row(5, 1.0, closed=False)]
    history = _run(_feed_serving(rows).history("BTCUSDT", "1h", "spot", 5))
    assert history[3].v is None
    sim = _sim()
    sim.warmup(history)
    assert sim.entry_filter.allows() is False
