"""Bounded, retry-aware runtime shared by every Anthropic call path."""
from __future__ import annotations

import atexit
import copy
import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future
from typing import Callable, TypeVar

import anthropic

from .observability import timed_operation

T = TypeVar("T")


class AiBusyError(RuntimeError):
    """Raised when all configured AI call slots are occupied."""


def ai_cache_key(namespace: str, prompt_version: str, model: str, payload) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    material = f"{namespace}\n{prompt_version}\n{model}\n{canonical}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _anthropic_transient(error: BaseException) -> bool:
    if isinstance(error, (anthropic.APIConnectionError, anthropic.RateLimitError)):
        return True
    return int(getattr(error, "status_code", 0) or 0) >= 500


class AiCallRuntime:
    """TTL/hash cache, same-key singleflight, retries, and bounded concurrency."""

    def __init__(
        self,
        *,
        max_concurrent: int | None = None,
        acquire_timeout_seconds: float | None = None,
        cache_ttl_seconds: float | None = None,
        cache_max_entries: int | None = None,
        retries: int | None = None,
        retry_backoff_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        is_transient: Callable[[BaseException], bool] = _anthropic_transient,
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        self._is_transient = is_transient
        self._cache_ttl = max(
            0.0,
            float(cache_ttl_seconds if cache_ttl_seconds is not None else os.environ.get("AI_CACHE_TTL_SECONDS", "900")),
        )
        self._cache_max = max(
            1,
            int(cache_max_entries if cache_max_entries is not None else os.environ.get("AI_CACHE_MAX_ENTRIES", "512")),
        )
        self._retries = max(0, int(retries if retries is not None else os.environ.get("AI_RETRIES", "1")))
        self._backoff = max(
            0.0,
            float(
                retry_backoff_seconds
                if retry_backoff_seconds is not None
                else os.environ.get("AI_RETRY_BACKOFF_SECONDS", "0.25")
            ),
        )
        self._acquire_timeout = max(
            0.0,
            float(
                acquire_timeout_seconds
                if acquire_timeout_seconds is not None
                else os.environ.get("AI_ACQUIRE_TIMEOUT_SECONDS", "0.1")
            ),
        )
        capacity = max(
            1,
            int(max_concurrent if max_concurrent is not None else os.environ.get("AI_MAX_CONCURRENT", "3")),
        )
        self._capacity = threading.BoundedSemaphore(capacity)
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._flights: dict[str, Future] = {}

    def _cache_get_locked(self, key: str):
        hit = self._cache.get(key)
        if hit is None:
            return None
        expires_at, value = hit
        if expires_at <= self._clock():
            self._cache.pop(key, None)
            return None
        self._cache.move_to_end(key)
        return copy.deepcopy(value)

    def _finish_failure(self, key: str, future: Future, error: BaseException) -> None:
        future.set_exception(error)
        # Observe it for the leader-only case; followers still receive it from result().
        future.exception()
        with self._lock:
            if self._flights.get(key) is future:
                self._flights.pop(key, None)

    def call(
        self,
        key: str,
        loader: Callable[[], T],
        *,
        retries: int | None = None,
    ) -> tuple[T, str]:
        retry_limit = self._retries if retries is None else max(0, int(retries))
        with self._lock:
            cached = self._cache_get_locked(key)
            if cached is not None:
                return cached, "cached"
            future = self._flights.get(key)
            if future is None:
                future = Future()
                self._flights[key] = future
                leader = True
            else:
                leader = False

        if not leader:
            return copy.deepcopy(future.result()), "shared"

        acquired = self._capacity.acquire(timeout=self._acquire_timeout)
        if not acquired:
            error = AiBusyError("AI 요청이 몰려 있어요. 잠시 후 다시 시도해 주세요.")
            self._finish_failure(key, future, error)
            raise error

        try:
            with timed_operation("ai"):
                attempt = 0
                while True:
                    try:
                        value = loader()
                        break
                    except BaseException as error:
                        if attempt >= retry_limit or not self._is_transient(error):
                            self._finish_failure(key, future, error)
                            raise
                        self._sleep(self._backoff * (2 ** attempt))
                        attempt += 1

            stored = copy.deepcopy(value)
            with self._lock:
                if self._cache_ttl > 0:
                    self._cache[key] = (self._clock() + self._cache_ttl, stored)
                    self._cache.move_to_end(key)
                    while len(self._cache) > self._cache_max:
                        self._cache.popitem(last=False)
                if self._flights.get(key) is future:
                    self._flights.pop(key, None)
            future.set_result(copy.deepcopy(stored))
            return copy.deepcopy(stored), "loaded"
        finally:
            self._capacity.release()

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


_client_lock = threading.Lock()
_client = None
_client_factory = None
_runtime: AiCallRuntime | None = None


def get_anthropic_client():
    global _client, _client_factory
    factory = anthropic.Anthropic
    with _client_lock:
        if _client is not None and _client_factory is factory:
            return _client
        if _client is not None:
            try:
                _client.close()
            except Exception:
                pass
        _client = factory(
            timeout=max(
                1.0,
                float(
                    os.environ.get(
                        "AI_TIMEOUT_SECONDS",
                        os.environ.get("ANTHROPIC_POSITION_NEWS_TIMEOUT_SECONDS", "15"),
                    )
                ),
            ),
            max_retries=0,
        )
        _client_factory = factory
        return _client


def get_ai_runtime() -> AiCallRuntime:
    global _runtime
    with _client_lock:
        if _runtime is None:
            _runtime = AiCallRuntime()
        return _runtime


def close_ai_runtime() -> None:
    global _client, _client_factory, _runtime
    with _client_lock:
        client = _client
        _client = None
        _client_factory = None
        runtime = _runtime
        _runtime = None
    if client is not None:
        try:
            client.close()
        except Exception:
            pass
    if runtime is not None:
        runtime.clear()


atexit.register(close_ai_runtime)
