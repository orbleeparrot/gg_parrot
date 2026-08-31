"""Shared outbound HTTP resources and cache-stampede coordination.

FastAPI runs the synchronous market-data handlers in worker threads.  A single
thread-safe ``httpx.Client`` lets those handlers reuse DNS lookups, TLS sessions,
and keep-alive connections instead of building a new pool for every poll.
"""
from __future__ import annotations

import atexit
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from typing import Callable, Mapping, TypeVar

import httpx

from .observability import record_timing

T = TypeVar("T")
_MISSING = object()

_client_lock = threading.Lock()
_client = None
_client_factory = None
_io_executor: ThreadPoolExecutor | None = None
_parallel_state = threading.local()


def _close_quietly(resource) -> None:
    try:
        resource.close()
    except Exception:
        pass


def _trace_http_request(request) -> None:
    request.extensions["ggp_started_at"] = time.perf_counter()


def _trace_http_response(response) -> None:
    started = response.request.extensions.pop("ggp_started_at", None)
    if started is not None:
        record_timing("upstream", (time.perf_counter() - started) * 1000.0)


def get_http_client():
    """Return the process-wide outbound client, creating it lazily.

    Remembering the factory identity keeps monkeypatched HTTP clients isolated
    in tests: when a test replaces ``httpx.Client``, the old pool is retired.
    """
    global _client, _client_factory
    factory = httpx.Client
    with _client_lock:
        if _client is not None and _client_factory is factory:
            return _client
        if _client is not None:
            _close_quietly(_client)
        timeout = float(os.environ.get("EXTERNAL_HTTP_TIMEOUT_SECONDS", "10"))
        max_connections = max(4, int(os.environ.get("EXTERNAL_HTTP_MAX_CONNECTIONS", "32")))
        keepalive = max(2, min(max_connections, int(os.environ.get("EXTERNAL_HTTP_KEEPALIVE_CONNECTIONS", "16"))))
        _client = factory(
            timeout=httpx.Timeout(timeout),
            follow_redirects=True,
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=keepalive,
                keepalive_expiry=30.0,
            ),
            headers={"User-Agent": "gg-parrot-backend/1.0"},
            event_hooks={
                "request": [_trace_http_request],
                "response": [_trace_http_response],
            },
        )
        _client_factory = factory
        return _client


def close_shared_http_client() -> None:
    global _client, _client_factory
    with _client_lock:
        if _client is not None:
            _close_quietly(_client)
        _client = None
        _client_factory = None


def _get_io_executor() -> ThreadPoolExecutor:
    global _io_executor
    with _client_lock:
        if _io_executor is None:
            workers = max(8, min(16, int(os.environ.get("EXTERNAL_HTTP_PARALLEL_WORKERS", "8"))))
            _io_executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ggp-http")
        return _io_executor


def _run_parallel_loader(context, loader: Callable[[], T]) -> T:
    previous = getattr(_parallel_state, "active", False)
    _parallel_state.active = True
    try:
        return context.run(loader)
    finally:
        _parallel_state.active = previous


def run_parallel(loaders: Mapping[str, Callable[[], T]]) -> dict[str, T]:
    """Run independent blocking I/O functions concurrently on a bounded pool."""
    if getattr(_parallel_state, "active", False):
        return {key: loader() for key, loader in loaders.items()}
    futures = {
        key: _get_io_executor().submit(_run_parallel_loader, copy_context(), loader)
        for key, loader in loaders.items()
    }
    return {key: future.result() for key, future in futures.items()}


def close_http_runtime() -> None:
    global _io_executor
    close_shared_http_client()
    with _client_lock:
        executor = _io_executor
        _io_executor = None
    if executor is not None:
        executor.shutdown(wait=False, cancel_futures=True)


class SingleFlightGroup:
    """Collapse concurrent loads for a key, with stale-while-revalidate support.

    A cold follower waits for the leader and receives its result.  When a stale
    value exists, followers return it immediately while the leader refreshes;
    this bounds upstream traffic without making every request wait on a slow API.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._flights: dict[object, Future] = {}

    def run(
        self,
        key: object,
        loader: Callable[[], T],
        *,
        stale_value=_MISSING,
    ) -> tuple[T, str]:
        with self._lock:
            future = self._flights.get(key)
            if future is not None:
                if stale_value is not _MISSING:
                    return stale_value, "stale"
                follower = True
            else:
                future = Future()
                self._flights[key] = future
                follower = False

        if follower:
            return future.result(), "shared"

        try:
            value = loader()
        except BaseException as exc:
            future.set_exception(exc)
            # Mark the exception as observed even when there were no followers.
            try:
                future.exception()
            except BaseException:
                pass
            raise
        else:
            future.set_result(value)
            return value, "loaded"
        finally:
            with self._lock:
                if self._flights.get(key) is future:
                    self._flights.pop(key, None)


atexit.register(close_http_runtime)
