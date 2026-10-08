"""Provider diagnostics must identify limits without exposing bodies or keys."""
import json
import logging
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import ai_provider_health, ai_runtime


def provider_error(*, headers=None, code="rate_limit_exceeded", error_type="tokens"):
    return openai.RateLimitError(
        "private provider message and article body",
        response=httpx.Response(429, headers=headers or {}, request=httpx.Request(
            "POST", "https://example.invalid", headers={"Authorization": "Bearer sk-proj-private-fixture-only"})),
        body={"code": code, "type": error_type, "message": "private prompt and source text"},
    )


def test_failed_sdk_call_logs_diagnostics_without_extra_requests(monkeypatch, caplog):
    events = []
    headers = {
        "x-request-id": "req_0123456789abcdef0123456789abcdef",
        "retry-after": "56",
        "retry-after-ms": "56000",
        "x-ratelimit-limit-requests": "60",
        "x-ratelimit-remaining-requests": "0",
        "x-ratelimit-reset-requests": "1s",
        "x-ratelimit-limit-tokens": "150000",
        "x-ratelimit-remaining-tokens": "0",
        "x-ratelimit-reset-tokens": "6m0s",
        "x-ratelimit-limit-project-tokens": "60000",
        "x-ratelimit-remaining-project-tokens": "0",
        "x-ratelimit-reset-project-tokens": "3s",
        "authorization": "Bearer sk-proj-private-fixture-only",
        "set-cookie": "private-cookie",
        "x-ratelimit-debug-prompt": "private prompt",
    }
    error = provider_error(headers=headers)

    def create(**_):
        events.append("request")
        raise error

    monkeypatch.setattr(ai_runtime, "record_openai_usage", lambda **_: events.append("usage"))
    monkeypatch.setattr(ai_provider_health, "pause", lambda _: events.append("pause"))
    messages = ai_runtime._Messages(SimpleNamespace(responses=SimpleNamespace(create=create)))
    with caplog.at_level(logging.WARNING, logger="app.ai_runtime"):
        with pytest.raises(ai_runtime.AiRateLimitError) as caught:
            messages.create(model="gpt-6-luna", max_tokens=600, messages=[], purpose="market_news_summary")
    record = next(r.getMessage() for r in caplog.records if "AI provider failure:" in r.getMessage())
    details = json.loads(record.split("diagnostics=", 1)[1])
    assert details["error_code"] == "rate_limit_exceeded"
    assert details["error_type"] == "tokens"
    assert details["request_id"] == headers["x-request-id"]
    assert details["headers"] == {k: v for k, v in headers.items() if k not in {
        "x-request-id", "authorization", "set-cookie", "x-ratelimit-debug-prompt"}}
    assert "purpose=market_news_summary" in record
    assert "http_status=429" in record
    assert all(secret not in record for secret in ["private", "sk-proj", "Bearer", "example.invalid"])
    assert events == ["request", "usage", "pause"]
    assert caught.value.__cause__ is error


@pytest.mark.parametrize("bad", ["sk-proj-private-fixture-only", "pnu_private_fixture_only", "private\nmessage", "x" * 200, {"secret": "private"}])
def test_untrusted_metadata_and_headers_are_omitted(bad):
    error = provider_error(headers={
        "x-request-id": "sk-proj-private-fixture-only",
        "retry-after": "private message",
        "x-ratelimit-limit-tokens": "Bearer sk-proj-private-fixture-only",
        "x-ratelimit-reset-tokens": "6m0s private prompt",
        "x-ratelimit-limit-project-tokens": "60000",
    }, code=bad, error_type=bad)
    details = ai_runtime._provider_failure_diagnostics(error)
    assert details == {"error_code": None, "error_type": None, "request_id": None,
                       "headers": {"x-ratelimit-limit-project-tokens": "60000"}}


def test_connection_failure_without_response_has_safe_empty_metadata():
    error = openai.APIConnectionError(message="private transport text", request=httpx.Request(
        "POST", "https://example.invalid", headers={"Authorization": "Bearer private"}))
    assert ai_runtime._provider_failure_diagnostics(error) == {
        "error_code": None, "error_type": None, "request_id": None, "headers": {}}


def test_uuid_request_id_fractional_wait_and_http_date_are_supported():
    details = ai_runtime._provider_failure_diagnostics(provider_error(headers={
        "x-request-id": "2123f0be-7100-400b-bd44-95ad093cd8bf",
        "retry-after": "0.5", "x-ratelimit-reset-tokens": "1m2.5s",
    }))
    assert details["request_id"] == "2123f0be-7100-400b-bd44-95ad093cd8bf"
    assert details["headers"] == {"retry-after": "0.5", "x-ratelimit-reset-tokens": "1m2.5s"}
    details = ai_runtime._provider_failure_diagnostics(provider_error(headers={
        "retry-after": "Wed, 07 Oct 2026 05:30:00 GMT"}))
    assert details["headers"] == {"retry-after": "Wed, 07 Oct 2026 05:30:00 GMT"}


def test_new_billing_code_is_visible_without_changing_retry_policy():
    error = provider_error(code="project_spend_limit_exceeded", error_type="insufficient_quota")
    details = ai_runtime._provider_failure_diagnostics(error)
    assert details["error_code"] == "project_spend_limit_exceeded"
    assert details["error_type"] == "insufficient_quota"
    assert isinstance(ai_runtime._translate_error(error), ai_runtime.AiRateLimitError)
