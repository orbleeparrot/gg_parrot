"""Single-process CPU isolation, singleflight, and TTL cache for optimize."""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import Future, ProcessPoolExecutor, TimeoutError as FutureTimeoutError
import copy
import multiprocessing
import os
import threading
import time
from typing import Callable

from .optimize import PreparedOptimization, run_prepared_optimization


class OptimizeBusyError(RuntimeError):
    """A distinct optimization already occupies the bounded worker capacity."""


class OptimizeTimeoutError(TimeoutError):
    """The request stopped waiting, while the worker remains registered in-flight."""


class OptimizeCoordinator:
    def __init__(
        self,
        *,
        executor=None,
        runner: Callable[[PreparedOptimization], dict] = run_prepared_optimization,
        max_in_flight: int = 1,
        cache_ttl_seconds: float = 300.0,
        cache_max_entries: int = 64,
        result_timeout_seconds: float = 60.0,
        process_workers: int = 1,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._executor = executor
        self._runner = runner
        self._max_in_flight = max(1, int(max_in_flight))
        self._cache_ttl = max(0.0, float(cache_ttl_seconds))
        self._cache_max = max(1, int(cache_max_entries))
        self._timeout = max(0.01, float(result_timeout_seconds))
        self._process_workers = max(1, int(process_workers))
        self._clock = clock
        self._lock = threading.RLock()
        self._in_flight: dict[str, Future] = {}
        self._cache: OrderedDict[str, tuple[float, dict]] = OrderedDict()
        self._closed = False

    def _get_executor(self):
        if self._executor is None:
            self._executor = ProcessPoolExecutor(
                max_workers=self._process_workers,
                mp_context=multiprocessing.get_context("spawn"),
            )
        return self._executor

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._cache.items() if expires_at <= now]
        for key in expired:
            self._cache.pop(key, None)

    def _finish(self, key: str, future: Future) -> None:
        try:
            result = future.result()
        except Exception:
            result = None
        with self._lock:
            if self._in_flight.get(key) is future:
                self._in_flight.pop(key, None)
            if result is not None and not self._closed:
                self._cache[key] = (self._clock() + self._cache_ttl, copy.deepcopy(result))
                self._cache.move_to_end(key)
                while len(self._cache) > self._cache_max:
                    self._cache.popitem(last=False)

    def run(self, prepared: PreparedOptimization) -> dict:
        key = prepared.cache_key
        with self._lock:
            if self._closed:
                raise RuntimeError("optimize coordinator is closed")
            now = self._clock()
            self._purge_expired(now)
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return copy.deepcopy(hit[1])

            future = self._in_flight.get(key)
            if future is None:
                if len(self._in_flight) >= self._max_in_flight:
                    raise OptimizeBusyError("다른 최적화가 실행 중입니다. 잠시 후 다시 시도해 주세요.")
                future = self._get_executor().submit(self._runner, prepared)
                self._in_flight[key] = future
                future.add_done_callback(lambda completed, cache_key=key: self._finish(cache_key, completed))

        try:
            return copy.deepcopy(future.result(timeout=self._timeout))
        except FutureTimeoutError as exc:
            raise OptimizeTimeoutError(
                "최적화가 제한 시간 안에 끝나지 않았습니다. 계산은 중복 실행하지 않고 정리 중입니다."
            ) from exc

    def shutdown(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            executor = self._executor
            self._executor = None
            self._cache.clear()
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


_default_lock = threading.Lock()
_default_coordinator: OptimizeCoordinator | None = None


def get_default_coordinator() -> OptimizeCoordinator:
    global _default_coordinator
    if _default_coordinator is None:
        with _default_lock:
            if _default_coordinator is None:
                _default_coordinator = OptimizeCoordinator(
                    max_in_flight=int(os.environ.get("OPTIMIZE_MAX_IN_FLIGHT", "1")),
                    cache_ttl_seconds=float(os.environ.get("OPTIMIZE_CACHE_TTL_SECONDS", "300")),
                    cache_max_entries=int(os.environ.get("OPTIMIZE_CACHE_MAX_ENTRIES", "64")),
                    result_timeout_seconds=float(os.environ.get("OPTIMIZE_TIMEOUT_SECONDS", "60")),
                    process_workers=int(os.environ.get("OPTIMIZE_PROCESS_WORKERS", "1")),
                )
    return _default_coordinator


def run(prepared: PreparedOptimization) -> dict:
    return get_default_coordinator().run(prepared)


def shutdown() -> None:
    global _default_coordinator
    with _default_lock:
        coordinator = _default_coordinator
        _default_coordinator = None
    if coordinator is not None:
        coordinator.shutdown()
