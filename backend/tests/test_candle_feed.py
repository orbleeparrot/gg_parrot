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

    sub = feed.subscribe("BTCUSDT", "5m", "spot", cb)
    key = ("BTCUSDT", "5m", "spot")
    delivered = _run(feed.poll_once(key))
    assert [c.c for c in delivered] == [1.0, 2.0]
    assert got == [("BTCUSDT", Candle(now - 900_000, 1.0, 1.0, 1.0, 1.0)), ("BTCUSDT", Candle(now - 600_000, 2.0, 2.0, 2.0, 2.0))]
    assert _run(feed.poll_once(key)) == []  # 같은 봉은 두 번 배달하지 않는다
    feed.unsubscribe(sub)
    assert key not in feed._subs


def test_callback_exception_does_not_break_other_subscribers():
    async def fetch(symbol, interval, limit, market):
        return [_k(1_700_000_000_000, 5.0)]

    feed = CandleFeed(fetch=fetch, now_ms=lambda: 1_700_000_900_000, sleep=lambda s: asyncio.sleep(0))
    seen = []

    async def bad(symbol, candle):
        raise RuntimeError("boom")

    async def good(symbol, candle):
        seen.append(candle.c)

    feed.subscribe("ETHUSDT", "1h", "spot", bad)
    feed.subscribe("ETHUSDT", "1h", "spot", good)
    _run(feed.poll_once(("ETHUSDT", "1h", "spot")))
    assert seen == [5.0]


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
