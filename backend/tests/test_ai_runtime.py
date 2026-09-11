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


def test_shared_ai_client_has_timeout_and_sdk_retries_disabled(monkeypatch):
    created = []

    class Client:
        def __init__(self, **kwargs):
            created.append(kwargs)

        def close(self):
            pass

    ai_runtime.close_ai_runtime()
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")
    monkeypatch.setattr(ai_runtime.genai, "Client", Client)
    assert ai_runtime.get_ai_client() is ai_runtime.get_ai_client()
    assert len(created) == 1
    http = created[0]["http_options"]
    assert http.timeout > 0  # milliseconds
    # The runtime owns retries; the SDK must not retry underneath it.
    assert http.retry_options.attempts <= 1
    ai_runtime.close_ai_runtime()


def test_shared_ai_client_refuses_to_start_without_a_key(monkeypatch):
    ai_runtime.close_ai_runtime()
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ai_runtime.AiAuthError):
        ai_runtime.get_ai_client()


def test_shared_ai_client_has_no_explicit_key_override():
    assert "api_key" not in signature(ai_runtime.get_ai_client).parameters


def test_all_ai_callers_use_the_guarded_runtime():
    # 호출 지점은 SDK 를 모른다 — 공급자를 바꿀 때 ai_runtime 한 곳만 손대기 위해서다.
    for module in (ai_explain, ai_challenge, news, classifier):
        source = getsource(module)
        assert "get_ai_runtime" in source, module.__name__
        assert "genai." not in source and "anthropic" not in source, module.__name__


# --- Gemini adapter: the only code that touches the SDK ---------------------
class _FakeModels:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return type("Resp", (), {"text": self.outcome})()


def _messages(outcome):
    models = _FakeModels(outcome)
    client = type("Client", (), {"models": models})()
    return ai_runtime._Messages(client), models


def test_adapter_maps_the_shared_contract_onto_generate_content():
    messages, models = _messages("답변껄")
    response = messages.create(
        model="gemini-3.5-flash-lite", max_tokens=321, system="시스템 지시",
        messages=[{"role": "user", "content": "질문"}], timeout=2.5,
    )
    assert [(b.type, b.text) for b in response.content] == [("text", "답변껄")]
    call = models.calls[0]
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["config"].system_instruction == "시스템 지시"
    assert call["config"].max_output_tokens == 321
    assert call["config"].http_options.timeout == 2500  # seconds in, milliseconds out
    turn = call["contents"][0]
    assert turn.role == "user" and turn.parts[0].text == "질문"


def test_adapter_turns_assistant_role_into_model_turn():
    messages, models = _messages("ok")
    messages.create(model="m", max_tokens=1, messages=[
        {"role": "user", "content": "a"}, {"role": "assistant", "content": "b"},
    ])
    assert [c.role for c in models.calls[0]["contents"]] == ["user", "model"]


def test_adapter_hands_back_an_empty_block_when_the_model_returns_nothing():
    messages, _ = _messages(None)  # blocked / empty answer
    response = messages.create(model="m", max_tokens=1, messages=[{"role": "user", "content": "x"}])
    assert response.content[0].text == ""


@pytest.mark.parametrize("code, expected, transient", [
    (401, ai_runtime.AiAuthError, False),
    (403, ai_runtime.AiAuthError, False),
    (429, ai_runtime.AiRateLimitError, True),
    (500, ai_runtime.AiStatusError, True),
    (400, ai_runtime.AiStatusError, False),
])
def test_adapter_translates_provider_errors(code, expected, transient):
    error = ai_runtime.genai_errors.APIError(code, {"error": {"message": "nope", "status": "X"}})
    messages, _ = _messages(error)
    with pytest.raises(expected) as raised:
        messages.create(model="m", max_tokens=1, messages=[{"role": "user", "content": "x"}])
    assert raised.value.status_code == code
    assert ai_runtime._provider_transient(raised.value) is transient


def test_adapter_reads_googles_400_invalid_key_as_an_auth_error():
    error = ai_runtime.genai_errors.APIError(
        400, {"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}},
    )
    messages, _ = _messages(error)
    with pytest.raises(ai_runtime.AiAuthError):
        messages.create(model="m", max_tokens=1, messages=[{"role": "user", "content": "x"}])


def test_adapter_treats_network_failures_as_transient():
    import httpx

    messages, _ = _messages(httpx.ConnectTimeout("slow"))
    with pytest.raises(ai_runtime.AiConnectionError) as raised:
        messages.create(model="m", max_tokens=1, messages=[{"role": "user", "content": "x"}])
    assert ai_runtime._provider_transient(raised.value) is True
