"""마감봉 피드 — 마감봉만, 한 번씩, 구독자 격리."""
import asyncio

import pytest

from app.engine.candle_feed import Candle, CandleFeed


def _k(t, c, closed=True):
    return {"t": t, "o": c, "h": c, "l": c, "c": c, "closed": closed}


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_next_close_ms_is_the_next_interval_boundary():
    assert CandleFeed.next_close_ms("5m", 1_700_000_000_000) == 1_700_000_100_000  # 1_700_000_000_000 은 5m 경계 아님
    assert CandleFeed.next_close_ms("1h", 1_700_002_800_000) == 1_700_006_400_000  # 경계 위면 다음 경계


def test_poll_once_delivers_only_new_closed_candles_in_order():
    calls = []
    now = 1_700_000_600_000
    rows = [_k(now - 900_000, 1.0), _k(now - 600_000, 2.0), _k(now - 300_000, 3.0, closed=False)]

    async def fetch(symbol, interval, limit, market):
        return rows

    feed = CandleFeed(fetch=fetch, now_ms=lambda: now, sleep=lambda s: asyncio.sleep(0))
    got = []

    async def cb(symbol, candle):
        got.append((symbol, candle))

    # since_t: 웜업(history)이 이미 소비했다고 가정하는 마지막 봉의 t. 두 마감봉보다
    # 더 과거로 주면(now - 1_200_000 은 4간격 전) 둘 다 "새 봉"으로 남는다.
    sub = feed.subscribe("BTCUSDT", "5m", "spot", cb, since_t=now - 1_200_000)
    key = ("BTCUSDT", "5m", "spot")
    # 실행 중인 이벤트 루프 밖에서 subscribe 했으므로 백그라운드 폴링 태스크는 없다 —
    # 이 테스트처럼 poll_once 를 직접 몰아서 부르는 호출자만 정상 동작한다.
    assert key not in feed._tasks
    delivered = _run(feed.poll_once(key))
    assert [c.c for c in delivered] == [1.0, 2.0]
    assert got == [("BTCUSDT", Candle(now - 900_000, 1.0, 1.0, 1.0, 1.0)), ("BTCUSDT", Candle(now - 600_000, 2.0, 2.0, 2.0, 2.0))]
    assert _run(feed.poll_once(key)) == []  # 같은 봉은 두 번 배달하지 않는다
    feed.unsubscribe(sub)
    assert key not in feed._subs


def test_subscribe_without_since_t_skips_already_closed_candles():
    now = 1_700_000_600_000
    rows = [_k(now - 900_000, 1.0), _k(now - 600_000, 2.0), _k(now - 300_000, 3.0, closed=False)]

    async def fetch(symbol, interval, limit, market):
        return rows

    feed = CandleFeed(fetch=fetch, now_ms=lambda: now, sleep=lambda s: asyncio.sleep(0))

    async def cb(symbol, candle):
        pass

    sub = feed.subscribe("BTCUSDT", "5m", "spot", cb)  # since_t 없음 — 웜업을 안 거친 구독
    key = ("BTCUSDT", "5m", "spot")
    # 이미 마감돼 있던 두 봉(now-900_000, now-600_000)은 웜업을 거치지 않았어도 다시
    # 배달되지 않는다 — 커서가 "지금 진행 중인 봉"의 바로 앞자리에서 시작하기 때문.
    assert _run(feed.poll_once(key)) == []

    # 커서 바로 다음 자리(= 지금 진행 중인 봉의 open time)가 마감되어 돌아오면 배달된다.
    open_t = CandleFeed.next_close_ms("5m", now) - 300_000  # 1_700_000_400_000
    rows[2] = _k(open_t, 9.0)
    delivered = _run(feed.poll_once(key))
    assert [c.c for c in delivered] == [9.0]
    feed.unsubscribe(sub)


def test_callback_exception_does_not_break_other_subscribers():
    async def fetch(symbol, interval, limit, market):
        return [_k(1_700_000_000_000, 5.0)]

    feed = CandleFeed(fetch=fetch, now_ms=lambda: 1_700_000_900_000, sleep=lambda s: asyncio.sleep(0))
    seen = []

    async def bad(symbol, candle):
        raise RuntimeError("boom")

    async def good(symbol, candle):
        seen.append(candle.c)

    # since_t=0: 이 테스트의 초점은 "구독자 격리" 지 커서 위치가 아니므로, 고정 fetch가
    # 돌려주는 봉이 항상 새 봉으로 잡히도록 커서를 충분히 과거로 둔다.
    feed.subscribe("ETHUSDT", "1h", "spot", bad, since_t=0)
    feed.subscribe("ETHUSDT", "1h", "spot", good, since_t=0)
    _run(feed.poll_once(("ETHUSDT", "1h", "spot")))
    assert seen == [5.0]


def test_late_subscriber_with_newer_since_t_skips_candles_it_already_warmed_up():
    """키 커서가 뒤처진 피드에 더 최근 since_t 로 합류한 구독자는 웜업에 포함된 봉을 다시 받지 않는다.
    먼저 있던 구독자는 그대로 받는다(키 커서는 건드리지 않음)."""
    now = 1_700_000_600_000
    t1, t2 = now - 900_000, now - 600_000
    rows = [_k(t1, 1.0), _k(t2, 2.0), _k(now - 300_000, 3.0, closed=False)]

    async def fetch(symbol, interval, limit, market):
        return rows

    feed = CandleFeed(fetch=fetch, now_ms=lambda: now, sleep=lambda s: asyncio.sleep(0))
    first, late = [], []

    async def cb_first(symbol, candle):
        first.append(candle.t)

    async def cb_late(symbol, candle):
        late.append(candle.t)

    key = ("BTCUSDT", "5m", "spot")
    feed.subscribe("BTCUSDT", "5m", "spot", cb_first, since_t=now - 1_200_000)  # 커서: 두 봉 다 아직 안 받음
    feed.subscribe("BTCUSDT", "5m", "spot", cb_late, since_t=t1)  # 웜업이 t1 까지 소비한 늦은 구독자
    assert feed._last_t[key] == now - 1_200_000  # 키 커서는 그대로
    _run(feed.poll_once(key))
    assert first == [t1, t2]
    assert late == [t2]  # t1 은 이미 웜업으로 봤으니 건너뛴다
    assert _run(feed.poll_once(key)) == []


def test_history_returns_closed_candles_oldest_first():
    async def fetch(symbol, interval, limit, market):
        assert limit == 4  # n + 1: 마지막 진행 중 봉을 뺀다
        return [_k(1, 1.0), _k(2, 2.0), _k(3, 3.0), _k(4, 4.0, closed=False)]

    feed = CandleFeed(fetch=fetch)
    assert [c.c for c in _run(feed.history("BTCUSDT", "5m", "spot", 3))] == [1.0, 2.0, 3.0]


def test_run_loop_stops_when_last_subscriber_leaves():
    async def fetch(symbol, interval, limit, market):
        return []

    async def scenario():
        feed = CandleFeed(fetch=fetch, now_ms=lambda: 1_700_000_000_000, sleep=lambda s: asyncio.sleep(0))

        async def cb(symbol, candle):
            pass

        sub = feed.subscribe("BTCUSDT", "1m", "spot", cb)
        task = feed._tasks[("BTCUSDT", "1m", "spot")]
        feed.unsubscribe(sub)
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()

    _run(scenario())
