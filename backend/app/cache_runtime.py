"""Bounded process caches with background refresh and observable failure cooldowns.

Only public projections belong here. Each caller chooses its acceptable stale
age; trading execution deliberately opts out of stale prices. Loader results
are published inside the flight, so even the first stale reader returns without
waiting and a late response after clear() cannot repopulate an invalidated cache.
"""
from __future__ import annotations

from collections import OrderedDict, Counter
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
from dataclasses import dataclass
import json
import threading
import time
import weakref

_registry = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()
_pool_lock = threading.Lock()
_pool = None
_slots = threading.BoundedSemaphore(32)


class CacheBusyError(RuntimeError):
    pass


@dataclass
class _Entry:
    value: object
    expires: float
    stale_until: float
    size: int


def _submit(loader):
    global _pool
    if not _slots.acquire(blocking=False):
        return False
    try:
        with _pool_lock:
            if _pool is None:
                _pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="ggp-cache-refresh")
            future = _pool.submit(copy_context().run, loader)
        future.add_done_callback(lambda _future: _slots.release())
        return True
    except Exception:
        _slots.release()
        return False


def close_cache_runtime():
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True)


def cache_statistics():
    """Finite names and numeric counters only; never expose cache keys or data."""
    with _registry_lock:
        caches = list(_registry.items())
    return {name: cache.statistics() for name, cache in caches}


def clear_all_caches():
    """Invalidate process projections (used by isolated tests and maintenance)."""
    with _registry_lock:
        caches = list(_registry.values())
    for cache in caches:
        cache.clear()


class ResponseCache:
    def __init__(self, name, *, max_entries=128, max_bytes=8_000_000,
                 retry_seconds=5, max_retry_seconds=60, clock=None):
        self.name = name
        self.max_entries = max(1, max_entries)
        self.max_bytes = max(1, max_bytes)
        self.retry_seconds = max(.1, retry_seconds)
        self.max_retry_seconds = max(self.retry_seconds, max_retry_seconds)
        self.clock = clock or time.monotonic
        self._entries = OrderedDict()
        self._failures = OrderedDict()
        self._flights = {}
        self._lock = threading.RLock()
        self._bytes = 0
        self._generation = 0
        self._counts = Counter()
        with _registry_lock:
            _registry[name] = self

    def _remove(self, key, reason):
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.size
            self._counts[reason] += 1

    def _purge(self, now):
        for key, entry in list(self._entries.items()):
            if entry.stale_until <= now:
                self._remove(key, "expired")
        for key, (_, retry_at, _) in list(self._failures.items()):
            if retry_at + self.max_retry_seconds * 2 <= now:
                self._failures.pop(key, None)

    def clear(self):
        with self._lock:
            self._generation += 1
            self._entries.clear()
            self._failures.clear()
            self._flights.clear()
            self._bytes = 0

    def invalidate(self, key):
        """Drop one projection and fence a loader already computing that key."""
        with self._lock:
            self._remove(key, "invalidated")
            self._failures.pop(key, None)
            self._flights.pop(key, None)

    def peek(self, key):
        """Inspect a usable local value without initiating I/O or extending age."""
        with self._lock:
            now = self.clock()
            self._purge(now)
            entry = self._entries.get(key)
            if entry is None:
                return None
            return deepcopy(entry.value), "cached" if entry.expires > now else "stale"

    def statistics(self):
        with self._lock:
            self._purge(self.clock())
            return {**{key: self._counts[key] for key in (
                "hit", "miss", "stale", "shared", "cooldown", "load", "load_error",
                "evicted", "expired", "oversized", "refresh_deferred")},
                "entries": len(self._entries), "size_bytes": self._bytes,
                "in_flight": len(self._flights), "max_entries": self.max_entries,
                "max_bytes": self.max_bytes}

    def get_or_load(self, key, loader, *, ttl, stale_ttl=0, now=None):
        stamp = self.clock() if now is None else now
        with self._lock:
            self._purge(stamp)
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
                if entry.expires > stamp:
                    self._counts["hit"] += 1
                    return deepcopy(entry.value), "cached"
            failure = self._failures.get(key)
            deferred = failure is not None and failure[1] > stamp
            future = self._flights.get(key)
            leader = future is None and not deferred
            if leader:
                if len(self._flights) >= 64:
                    if entry is None:
                        raise CacheBusyError("Public cache preparation is busy")
                    self._counts["refresh_deferred"] += 1
                    return deepcopy(entry.value), "stale"
                future = Future()
                self._flights[key] = future
            if entry is not None:
                self._counts["stale"] += 1
                if deferred:
                    self._counts["cooldown"] += 1
                if leader:
                    generation = self._generation
                    if not _submit(lambda: self._load(key, loader, future, generation, ttl, stale_ttl, now)):
                        self._flights.pop(key, None)
                        self._counts["refresh_deferred"] += 1
                return deepcopy(entry.value), "stale"
            if deferred:
                self._counts["cooldown"] += 1
                raise failure[0].with_traceback(None)
            if not leader:
                self._counts["shared"] += 1
            else:
                self._counts["miss"] += 1
                generation = self._generation
        if leader:
            self._load(key, loader, future, generation, ttl, stale_ttl, now)
        return deepcopy(future.result()), "loaded" if leader else "shared"

    def _load(self, key, loader, future, generation, ttl, stale_ttl, now):
        try:
            value = loader()
            # Payloads in these caches are JSON projections. Count serialized
            # UTF-8 bytes (not an exact allocator/RSS measurement).
            size = len(json.dumps(value, ensure_ascii=False, default=str).encode())
            stored = deepcopy(value)
            stamp = self.clock() if now is None else now
            with self._lock:
                if generation == self._generation and self._flights.get(key) is future:
                    self._counts["load"] += 1
                    self._failures.pop(key, None)
                    self._remove(key, "replaced")
                    self._purge(stamp)
                    if ttl > 0 and size <= self.max_bytes:
                        self._entries[key] = _Entry(stored, stamp + ttl, stamp + ttl + stale_ttl, size)
                        self._bytes += size
                        while len(self._entries) > self.max_entries or self._bytes > self.max_bytes:
                            self._remove(next(iter(self._entries)), "evicted")
                    elif size > self.max_bytes:
                        self._counts["oversized"] += 1
            future.set_result(stored)
        except BaseException as error:
            stamp = self.clock() if now is None else now
            with self._lock:
                if generation == self._generation and self._flights.get(key) is future:
                    previous = self._failures.get(key)
                    attempts = min(8, previous[2] + 1) if previous else 1
                    delay = min(self.max_retry_seconds, self.retry_seconds * 2 ** (attempts - 1))
                    self._failures[key] = (error.with_traceback(None), stamp + delay, attempts)
                    while len(self._failures) > self.max_entries:
                        self._failures.popitem(last=False)
                    self._counts["load_error"] += 1
            future.set_exception(error)
            future.exception()  # Background leader may have no waiting readers.
        finally:
            with self._lock:
                if self._flights.get(key) is future:
                    self._flights.pop(key, None)
