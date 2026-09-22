"""마감봉 피드 — 바이낸스 마감봉을 (종목·간격·시장)별로 한 번씩 받아 구독자에게 밀어 준다.

캔들형 전략(D~K)은 봉 마감에만 판단한다. 페이퍼·실행기 세션이 같은 봉을 구독하므로 백테스트와
같은 봉으로 돈다. 진행 중 봉은 절대 배달하지 않는다(``closed`` 만 믿는다).
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import namedtuple
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from ..data.binance import _INTERVAL_MS, get_recent_klines

log = logging.getLogger(__name__)

Candle = namedtuple("Candle", "t o h l c")
Key = Tuple[str, str, str]  # (symbol, interval, market)
Callback = Callable[[str, Candle], Awaitable[None]]

GRACE_SECONDS = float(os.environ.get("CANDLE_GRACE_SECONDS", "2"))
RETRY_SECONDS = float(os.environ.get("CANDLE_RETRY_SECONDS", "30"))
RETRY_STEP_SECONDS = 2.0


class Subscription:
    __slots__ = ("key", "callback")

    def __init__(self, key: Key, callback: Callback) -> None:
        self.key = key
        self.callback = callback


async def _default_fetch(symbol: str, interval: str, limit: int, market: str) -> list[dict]:
    return await asyncio.to_thread(get_recent_klines, symbol, interval, limit, market=market)


def _to_candle(row: dict) -> Candle:
    return Candle(int(row["t"]), float(row["o"]), float(row["h"]), float(row["l"]), float(row["c"]))


class CandleFeed:
    def __init__(self, *, fetch=None, now_ms=None, sleep=None) -> None:
        self._fetch = fetch or _default_fetch
        self._now_ms = now_ms or (lambda: int(time.time() * 1000))
        self._sleep = sleep or asyncio.sleep
        self._subs: Dict[Key, List[Subscription]] = {}
        self._tasks: Dict[Key, asyncio.Task] = {}
        self._last_t: Dict[Key, int] = {}
        self._helper_loops: Dict[Key, asyncio.AbstractEventLoop] = {}

    # -- 구독 -----------------------------------------------------------
    def subscribe(self, symbol: str, interval: str, market: str, callback: Callback) -> Subscription:
        key: Key = (symbol.upper(), interval, market)
        sub = Subscription(key, callback)
        self._subs.setdefault(key, []).append(sub)
        if key not in self._tasks:
            # 최초 poll_once 는 _last_t 미기록(-1 기본값)이라 그 시점의 fetch(limit=3)에
            # 담긴 마감봉을 그대로 새 봉으로 배달한다 — 깊은 웜업은 history 로 따로 받는다.
            # subscribe()는 실행 중인 이벤트 루프 밖(동기 문맥)에서도 불릴 수 있다 —
            # 그럴 땐 새 루프를 만들어 태스크를 얹어 두고, unsubscribe 에서 정리한다.
            try:
                loop = asyncio.get_running_loop()
                helper = None
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                helper = loop
            self._tasks[key] = loop.create_task(self._run(key))
            if helper is not None:
                self._helper_loops[key] = helper
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.key)
        if not subs:
            return
        if sub in subs:
            subs.remove(sub)
        if not subs:
            self._subs.pop(sub.key, None)
            self._last_t.pop(sub.key, None)
            task = self._tasks.pop(sub.key, None)
            if task is not None:
                task.cancel()
            helper = self._helper_loops.pop(sub.key, None)
            if helper is not None:
                # 태스크가 실제로 돈 적 없는 헬퍼 루프라도, 취소된 코루틴을 한 번
                # 돌려 정리한 뒤 닫아야 "coroutine was never awaited" 경고가 없다.
                if task is not None:
                    try:
                        helper.run_until_complete(task)
                    except (asyncio.CancelledError, Exception):
                        pass
                helper.close()

    # -- 시각 -----------------------------------------------------------
    @staticmethod
    def next_close_ms(interval: str, now_ms: int) -> int:
        step = _INTERVAL_MS[interval]
        return (now_ms // step + 1) * step

    # -- 조회 -----------------------------------------------------------
    async def history(self, symbol: str, interval: str, market: str, n: int) -> List[Candle]:
        n = max(1, min(int(n), 999))
        rows = await self._fetch(symbol.upper(), interval, n + 1, market)
        closed = [_to_candle(r) for r in rows if r.get("closed")]
        return closed[-n:]

    async def poll_once(self, key: Key) -> List[Candle]:
        symbol, interval, market = key
        rows = await self._fetch(symbol, interval, 3, market)
        last = self._last_t.get(key, -1)
        fresh = sorted((_to_candle(r) for r in rows if r.get("closed") and int(r["t"]) > last), key=lambda c: c.t)
        for candle in fresh:
            self._last_t[key] = candle.t
            for sub in list(self._subs.get(key, [])):
                try:
                    await sub.callback(symbol, candle)
                except Exception:
                    log.exception("candle feed: subscriber failed for %s %s", symbol, interval)
        return fresh

    # -- 루프 -----------------------------------------------------------
    async def _run(self, key: Key) -> None:
        symbol, interval, market = key
        try:
            while key in self._subs:
                target = self.next_close_ms(interval, self._now_ms()) + int(GRACE_SECONDS * 1000)
                await self._sleep(max(0.0, (target - self._now_ms()) / 1000.0))
                deadline = self._now_ms() + int(RETRY_SECONDS * 1000)
                while key in self._subs:
                    try:
                        if await self.poll_once(key):
                            break
                    except Exception:
                        log.exception("candle feed: fetch failed for %s %s", symbol, interval)
                    if self._now_ms() >= deadline:
                        break  # 이번 봉은 다음 라운드의 limit=3 으로 따라잡는다
                    await self._sleep(RETRY_STEP_SECONDS)
        except asyncio.CancelledError:
            raise


feed = CandleFeed()
