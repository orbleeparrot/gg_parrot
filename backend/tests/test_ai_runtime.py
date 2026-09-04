from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from inspect import getsource, signature

import pytest

from app import ai_challenge, ai_explain, news
from app import ai_runtime
from app.agent_features.position_news import classifier


def test_hash_key_is_canonical_and_prompt_versioned():
    first = ai_runtime.ai_cache_key("summary", "v1", "model-a", {"b": 2, "a": 1})
    reordered = ai_runtime.ai_cache_key("summary", "v1", "model-a", {"a": 1, "b": 2})
    changed = ai_runtime.ai_cache_key("summary", "v2", "model-a", {"a": 1, "b": 2})
    assert first == reordered
    assert first != changed


def test_success_is_cached_until_ttl_and_returned_as_a_copy():
    now = [10.0]
    runtime = ai_runtime.AiCallRuntime(clock=lambda: now[0], cache_ttl_seconds=5)
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        return {"items": [1]}

    first, first_state = runtime.call("same", load)
    first["items"].append(2)
    second, second_state = runtime.call("same", load)
    now[0] = 16.0
    third, third_state = runtime.call("same", load)

    assert (first_state, second_state, third_state) == ("loaded", "cached", "loaded")
    assert second == {"items": [1]}
    assert third == {"items": [1]}
    assert calls == 2


def test_identical_concurrent_ai_calls_share_one_future():
    runtime = ai_runtime.AiCallRuntime(max_concurrent=2)
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def load():
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(1)
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(runtime.call, "same", load)
        assert entered.wait(1)
        second = pool.submit(runtime.call, "same", load)
        release.set()
        results = [first.result(timeout=1), second.result(timeout=1)]

    assert calls == 1
    assert {state for _value, state in results} == {"loaded", "shared"}


def test_distinct_ai_call_is_rejected_when_capacity_is_full():
    runtime = ai_runtime.AiCallRuntime(max_concurrent=1, acquire_timeout_seconds=0.01)
    entered = threading.Event()
    release = threading.Event()

    def slow():
        entered.set()
        assert release.wait(1)
        return "first"

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(runtime.call, "first", slow)
        assert entered.wait(1)
        with pytest.raises(ai_runtime.AiBusyError):
            runtime.call("second", lambda: "second")
        release.set()
        assert first.result(timeout=1)[0] == "first"


def test_default_ai_queue_fails_fast_instead_of_exhausting_request_threads(monkeypatch):
    monkeypatch.delenv("AI_ACQUIRE_TIMEOUT_SECONDS", raising=False)

    runtime = ai_runtime.AiCallRuntime()

    assert runtime._acquire_timeout <= 1


def test_transient_failure_retries_once_but_permanent_failure_does_not():
    sleeps = []
    runtime = ai_runtime.AiCallRuntime(
        retries=1,
        retry_backoff_seconds=0.25,
        sleeper=sleeps.append,
        is_transient=lambda error: isinstance(error, TimeoutError),
    )
    attempts = 0

    def flaky():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("slow")
        return "ok"

    assert runtime.call("retry", flaky)[0] == "ok"
    assert attempts == 2
    assert sleeps == [0.25]

    permanent_attempts = 0

    def permanent():
        nonlocal permanent_attempts
        permanent_attempts += 1
        raise ValueError("bad request")

    with pytest.raises(ValueError):
        runtime.call("permanent", permanent)
    assert permanent_attempts == 1


def test_per_call_retry_override_can_disable_global_retry():
    sleeps = []
    runtime = ai_runtime.AiCallRuntime(
        retries=1,
        sleeper=sleeps.append,
        is_transient=lambda _error: True,
    )
    attempts = 0

    def fail():
        nonlocal attempts
        attempts += 1
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        runtime.call("no-retry", fail, retries=0)

    assert attempts == 1
    assert sleeps == []


def test_position_news_classifier_disables_runtime_retries(monkeypatch):
    observed = {}

    class Runtime:
        def call(self, _key, _loader, **kwargs):
            observed.update(kwargs)
            return {
                "items": [{
                    "sentiment": "neutral",
                    "reason": "테스트",
                    "confidence": "medium",
                }],
            }, "loaded"

    monkeypatch.setattr(classifier, "get_ai_runtime", lambda: Runtime())

    result = classifier._generate_ai_analysis(
        [{"title": "BTC market update", "source": "Test"}],
        "비트코인",
    )

    assert result["items"][0]["sentiment"] == "neutral"
    assert observed["retries"] == 0


def test_failed_call_is_not_cached_and_releases_capacity():
    runtime = ai_runtime.AiCallRuntime(max_concurrent=1, retries=0)
    with pytest.raises(RuntimeError):
        runtime.call("key", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert runtime.call("key", lambda: "recovered") == ("recovered", "loaded")


def test_shared_anthropic_client_has_timeout_and_sdk_retries_disabled(monkeypatch):
    created = []

    class Client:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def close(self):
            pass

    ai_runtime.close_ai_runtime()
    monkeypatch.setattr(ai_runtime.anthropic, "Anthropic", Client)
    assert ai_runtime.get_anthropic_client() is ai_runtime.get_anthropic_client()
    assert len(created) == 1
    assert created[0]["timeout"] > 0
    assert created[0]["max_retries"] == 0
    ai_runtime.close_ai_runtime()


def test_shared_anthropic_client_has_no_explicit_key_override():
    assert "api_key" not in signature(ai_runtime.get_anthropic_client).parameters


def test_all_ai_callers_use_the_guarded_runtime():
    for module in (ai_explain, ai_challenge, news, classifier):
        source = getsource(module)
        assert "get_ai_runtime" in source, module.__name__
        assert "anthropic.Anthropic(" not in source, module.__name__
