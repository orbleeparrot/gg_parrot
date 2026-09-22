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
    __slots__ = ("key", "callback", "since_t")

    def __init__(self, key: Key, callback: Callback, since_t: Optional[int] = None) -> None:
        self.key = key
        self.callback = callback
        # 이 구독자가 이미 소비한(웜업한) 마지막 봉의 t. 키 커서(_last_t)가 이보다 뒤처져 있으면
        # 그 사이 봉은 이 구독자에게만 건너뛴다 — 웜업에 포함된 봉을 다시 받으면 지표가 이중 누적된다.
        self.since_t = since_t


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

    # -- 구독 -----------------------------------------------------------
    def subscribe(
        self, symbol: str, interval: str, market: str, callback: Callback,
        *, since_t: Optional[int] = None,
    ) -> Subscription:
        """새 구독을 등록한다.

        ``since_t`` — 호출자가 ``history()`` 로 웜업한 마지막 봉의 open time(ms).
        이미 소비한 봉을 poll_once 가 다시 배달하면 지표(RSI/MA 등) 상태가 중복
        누적되고 체결이 겹칠 수 있다. 새 구독이면 이 값을 커서로 삼아 그 봉까지는
        건너뛰고, 그 뒤에 마감되는 봉부터만 새 봉으로 취급한다. 값을 안 주면
        지금 진행 중인 봉만 다음 마감 대상으로 본다(그 전에 이미 마감된 봉은 전부
        건너뜀 — 다음 마감 시각에서 한 간격을 뺀 자리가 "지금 진행 중인 봉의 open
        time - 1ms" 이므로, 그 봉이 마감되어야 비로소 배달된다).
        """
        key: Key = (symbol.upper(), interval, market)
        sub = Subscription(key, callback, since_t)
        is_new = key not in self._subs  # 이 키의 첫 구독자인지 — _tasks 는 루프가 없으면
        # 영영 채워지지 않을 수 있으므로 "새 키" 판정 기준으로 쓸 수 없다.
        self._subs.setdefault(key, []).append(sub)
        if is_new:
            self._last_t[key] = (
                since_t if since_t is not None
                else self.next_close_ms(interval, self._now_ms()) - _INTERVAL_MS[interval] - 1
            )
            # 실행 중인 이벤트 루프가 없으면 백그라운드 폴링 태스크를 얹을 곳이 없다.
            # 숨겨진 헬퍼 루프를 새로 만들어 얹어봤자 아무도 돌리지 않으면 그저 새는
            # 자원일 뿐이다 — 조용히 실패하는 대신 경고를 남기고 태스크 생성을 건너뛴다.
            # 구독/커서는 그대로 등록되므로, poll_once 를 직접 호출하는 호출자(테스트 등)는
            # 정상 동작한다.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                log.warning(
                    "candle feed: no running event loop — %s %s will not be polled until "
                    "subscribed from the loop", symbol, interval,
                )
            else:
                self._tasks[key] = loop.create_task(self._run(key))
        # 이미 도는 피드에 다른 구독자가 (다른) since_t 로 합류하는 경우: 키 커서는
        # 이미 돌고 있는 피드가 기준이다. since_t 로 뒤로 돌리면 먼저 있던 구독자가
        # 이미 받은 봉을 다시 받게 되므로 기존 커서는 그대로 둔다. 반대로 키 커서가 새 구독자의
        # since_t 보다 뒤처져 있으면(먼저 온 구독자가 아직 못 받은 봉을 새 구독자는 웜업으로 이미
        # 소비한 경우) poll_once 가 구독자별 since_t 로 그 봉을 새 구독자에게만 건너뛴다.
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
            # 콜백 안에서 unsubscribe 되어 마지막 구독자가 사라지면 _last_t 항목도 함께
            # 지워진다 — 그 뒤에 여기서 다시 써 넣으면 이미 정리된 키가 되살아난다.
            if key in self._subs:
                self._last_t[key] = candle.t
            for sub in list(self._subs.get(key, [])):
                if sub.since_t is not None and candle.t <= sub.since_t:
                    continue  # 이 구독자는 웜업으로 이미 본 봉 — 두 번 주지 않는다
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
