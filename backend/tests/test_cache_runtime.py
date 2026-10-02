from concurrent.futures import Future, ThreadPoolExecutor
import threading

import pytest

from app.cache_runtime import CacheBusyError, ResponseCache, close_cache_runtime


def test_first_stale_reader_returns_before_refresh_and_followers_share_it():
    clock = [0]
    cache = ResponseCache("test-swr", clock=lambda: clock[0])
    cache.get_or_load("x", lambda: {"value": 1}, ttl=2, stale_ttl=10)
    clock[0] = 3
    entered, release = threading.Event(), threading.Event()
    calls = []

    def load():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return {"value": 2}

    try:
        first, state = cache.get_or_load("x", load, ttl=2, stale_ttl=10)
        assert first == {"value": 1} and state == "stale"
        assert entered.wait(1)
        for _ in range(5):
            assert cache.get_or_load("x", load, ttl=2, stale_ttl=10) == ({"value": 1}, "stale")
        assert len(calls) == 1
    finally:
        release.set()
        close_cache_runtime()
    assert cache.get_or_load("x", load, ttl=2)[0] == {"value": 2}


def test_failures_back_off_and_stale_values_have_a_hard_age_limit():
    clock = [0]
    cache = ResponseCache("test-failure", clock=lambda: clock[0], retry_seconds=5)
    cache.get_or_load("x", lambda: 7, ttl=1, stale_ttl=2)
    clock[0] = 2
    calls = []

    def fail():
        calls.append(1)
        raise ValueError("unavailable")

    assert cache.get_or_load("x", fail, ttl=1, stale_ttl=2) == (7, "stale")
    close_cache_runtime()
    for _ in range(5):
        assert cache.get_or_load("x", fail, ttl=1, stale_ttl=2) == (7, "stale")
    assert len(calls) == 1
    clock[0] = 4  # no longer allowed to present that old value
    with pytest.raises(ValueError):
        cache.get_or_load("x", fail, ttl=1)
    assert len(calls) == 1
    clock[0] = 8
    with pytest.raises(ValueError):
        cache.get_or_load("x", fail, ttl=1)
    assert len(calls) == 2


def test_entry_and_byte_limits_expiry_and_mutation_isolation():
    clock = [0]
    cache = ResponseCache("test-bounds", max_entries=3, max_bytes=32, clock=lambda: clock[0])
    first, _ = cache.get_or_load("x", lambda: [1], ttl=1)
    first.append(99)
    assert cache.get_or_load("x", lambda: [], ttl=1)[0] == [1]
    for key in range(291):
        cache.get_or_load(key, lambda: [12345], ttl=1)
    assert cache.statistics()["entries"] <= 3
    assert cache.statistics()["size_bytes"] <= 32
    assert cache.statistics()["evicted"] > 0
    clock[0] = 2
    assert cache.statistics()["entries"] == 0
    cache.get_or_load("large", lambda: "a" * 100, ttl=1)
    assert cache.statistics()["entries"] == 0
    assert cache.statistics()["oversized"] == 1


@pytest.mark.parametrize("clear", [False, True])
def test_invalidated_inflight_result_cannot_repopulate_cache(clear):
    cache = ResponseCache("test-invalidation")
    entered, release = threading.Event(), threading.Event()
    def old():
        entered.set()
        assert release.wait(2)
        return "old"
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(cache.get_or_load, "x", old, ttl=60)
        try:
            assert entered.wait(1)
            cache.clear() if clear else cache.invalidate("x")
            assert cache.get_or_load("x", lambda: "new", ttl=60)[0] == "new"
        finally:
            release.set()
        future.result(timeout=1)
    assert cache.get_or_load("x", lambda: "unexpected", ttl=60)[0] == "new"


def test_cold_failure_is_cached_without_retaining_a_successful_empty_value():
    cache = ResponseCache("test-cold-backoff")
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError("source down")
    for _ in range(5):
        with pytest.raises(RuntimeError):
            cache.get_or_load("x", fail, ttl=10)
    assert len(calls) == 1 and cache.statistics()["cooldown"] == 4


def test_waiting_refresh_returns_new_value_once_to_many_expired_readers():
    clock = [0]
    cache = ResponseCache("test-wait-refresh", clock=lambda: clock[0])
    cache.get_or_load("x", lambda: {"value": 1}, ttl=2, stale_ttl=10)
    clock[0] = 2
    entered, release = threading.Event(), threading.Event()
    calls = []
    def load():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return {"value": 2}
    with ThreadPoolExecutor(max_workers=6) as pool:
        first = pool.submit(cache.get_or_load, "x", load, ttl=2, stale_ttl=10, wait_for_refresh=True)
        try:
            assert entered.wait(1)
            assert not first.done()  # A normal refresh is not stale data.
            followers = [pool.submit(cache.get_or_load, "x", load, ttl=2, stale_ttl=10,
                                     wait_for_refresh=True) for _ in range(5)]
        finally:
            release.set()
        results = [first.result(timeout=1), *(future.result(timeout=1) for future in followers)]
    assert len(calls) == 1
    assert all(value == {"value": 2} and state != "stale" for value, state in results)
    assert results[0][1] == "loaded"


def test_waiting_reader_joins_an_existing_background_refresh():
    clock = [0]
    cache = ResponseCache("test-wait-joins-swr", clock=lambda: clock[0])
    cache.get_or_load("x", lambda: 1, ttl=1, stale_ttl=10)
    clock[0] = 1
    entered, release = threading.Event(), threading.Event()
    calls = []
    def load():
        calls.append(1)
        entered.set()
        assert release.wait(2)
        return 2
    try:
        assert cache.get_or_load("x", load, ttl=1, stale_ttl=10) == (1, "stale")
        assert entered.wait(1)
        with ThreadPoolExecutor(max_workers=1) as pool:
            waiting = pool.submit(cache.get_or_load, "x", load, ttl=1, stale_ttl=10,
                                  wait_for_refresh=True)
            release.set()
            value, state = waiting.result(timeout=1)
        assert value == 2 and state != "stale"
        assert len(calls) == 1
    finally:
        release.set()
        close_cache_runtime()


def test_waiting_refresh_failure_uses_stale_only_during_its_original_age_and_cooldown():
    clock = [0]
    cache = ResponseCache("test-wait-failure", clock=lambda: clock[0], retry_seconds=5)
    cache.get_or_load("x", lambda: 7, ttl=1, stale_ttl=2)
    clock[0] = 1
    calls = []
    def fail():
        calls.append(1)
        raise ValueError("source unavailable")

    assert cache.get_or_load("x", fail, ttl=1, stale_ttl=2, wait_for_refresh=True) == (7, "stale")
    clock[0] = 2
    for _ in range(3):
        assert cache.get_or_load("x", fail, ttl=1, stale_ttl=2, wait_for_refresh=True) == (7, "stale")
    assert len(calls) == 1
    clock[0] = 3
    with pytest.raises(ValueError, match="source unavailable"):
        cache.get_or_load("x", fail, ttl=1, stale_ttl=2, wait_for_refresh=True)
    assert len(calls) == 1  # Expiry cannot extend stale life during backoff.
    clock[0] = 6
    assert cache.get_or_load("x", lambda: 9, ttl=1, stale_ttl=2, wait_for_refresh=True) == (9, "loaded")


@pytest.mark.parametrize("invalidate", [False, True])
def test_waiting_failure_rechecks_age_and_invalidation_before_stale_fallback(invalidate):
    clock = [0]
    cache = ResponseCache("test-wait-recheck", clock=lambda: clock[0])
    cache.get_or_load("x", lambda: 7, ttl=1, stale_ttl=2)
    clock[0] = 1
    entered, release = threading.Event(), threading.Event()
    def fail_late():
        entered.set()
        assert release.wait(2)
        raise ValueError("late failure")
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(cache.get_or_load, "x", fail_late, ttl=1, stale_ttl=2,
                              wait_for_refresh=True)
        try:
            assert entered.wait(1)
            if invalidate:
                cache.invalidate("x")
            else:
                clock[0] = 3
        finally:
            release.set()
        with pytest.raises(ValueError, match="late failure"):
            waiting.result(timeout=1)


def test_waiting_refresh_capacity_deferral_cannot_extend_old_data_or_start_duplicate_io():
    clock = [0]
    cache = ResponseCache("test-wait-capacity", clock=lambda: clock[0])
    cache.get_or_load("x", lambda: 7, ttl=1, stale_ttl=2)
    cache._flights.update({f"busy-{index}": Future() for index in range(64)})
    def unused():
        pytest.fail("A full cache cannot start another upstream request.")
    clock[0] = 1
    assert cache.get_or_load("x", unused, ttl=1, stale_ttl=2, wait_for_refresh=True) == (7, "stale")
    assert cache.statistics()["refresh_deferred"] == 1
    clock[0] = 3
    with pytest.raises(CacheBusyError):
        cache.get_or_load("x", unused, ttl=1, stale_ttl=2, wait_for_refresh=True)
