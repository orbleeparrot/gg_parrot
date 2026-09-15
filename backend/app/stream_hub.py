"""프로세스 안 fan-out 허브 — 동기 핸들러(스레드)가 바꾼 사실을 비동기 스트림(SSE·WebSocket)에 깨워 준다.

runner.py 의 세션 스트림 허브와 같은 구조를 알림 스트림도 쓰도록 떼어 낸 것이다. 값은
들고 있지 않는다: 깨어난 쪽이 DB 에서 다시 읽는다(허브는 "바뀌었다"만 전한다).
"""
from __future__ import annotations

import asyncio
import threading


class StreamHub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_id = 0
        self._subscribers: dict[int, dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Event]]] = {}

    def subscribe(self, user_id: int) -> tuple[int, asyncio.Event]:
        """실행 중인 이벤트 루프에서 부른다. (구독 id, 깨울 이벤트)를 돌려준다."""
        loop = asyncio.get_running_loop()
        event = asyncio.Event()
        with self._lock:
            self._next_id += 1
            subscription_id = self._next_id
            self._subscribers.setdefault(user_id, {})[subscription_id] = (loop, event)
        return subscription_id, event

    def unsubscribe(self, user_id: int, subscription_id: int) -> None:
        with self._lock:
            account = self._subscribers.get(user_id)
            if account is None:
                return
            account.pop(subscription_id, None)
            if not account:
                self._subscribers.pop(user_id, None)

    def notify(self, user_id: int) -> int:
        """어느 스레드에서든 부를 수 있다. 깨운 구독 수를 돌려준다."""
        with self._lock:
            subscribers = list(self._subscribers.get(user_id, {}).values())
        return self._wake(subscribers)

    def notify_all(self) -> int:
        with self._lock:
            subscribers = [pair for account in self._subscribers.values() for pair in account.values()]
        return self._wake(subscribers)

    def subscriber_count(self, user_id: int | None = None) -> int:
        with self._lock:
            if user_id is not None:
                return len(self._subscribers.get(user_id, {}))
            return sum(len(account) for account in self._subscribers.values())

    @staticmethod
    def _wake(subscribers) -> int:
        woken = 0
        for loop, event in subscribers:
            try:
                loop.call_soon_threadsafe(event.set)
                woken += 1
            except RuntimeError:
                # 스트림의 루프가 이미 닫혔다 — 그쪽 finally 가 구독을 지운다.
                continue
        return woken
