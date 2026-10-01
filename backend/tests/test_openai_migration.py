"""No network: cover every production AI call site and the real SDK wire contract."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest

from app import ai_runtime, api_usage


def test_no_old_provider_sdk_or_key_gates_remain():
    root = Path(__file__).resolve().parents[1] / "app"
    callsites = {}
    for path in root.rglob("*.py"):
        text = path.read_text()
        assert "GEMINI_API_KEY" not in text, path
        assert "ANTHROPIC_API_KEY" not in text, path
        assert "generate_content(" not in text, path
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(("google.genai", "anthropic")), path
            if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("messages.create"):
                assert ast.unparse(node.func) == "get_ai_client().messages.create", path
                assert "get_ai_runtime" in text, path
                callsites[str(path.relative_to(root))] = callsites.get(str(path.relative_to(root)), 0) + 1
    assert callsites == {
        "ai_explain.py": 1, "ai_challenge.py": 1, "news.py": 2,
        "community_summaries.py": 1, "ask.py": 2,
        "agent_features/position_news/classifier.py": 1,
    }


def test_only_openai_key_and_model_are_used(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "ignored")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ignored")
    monkeypatch.setenv("GEMINI_MODEL", "ignored")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    assert not ai_runtime.ai_available()
    assert ai_runtime.default_model() == "gpt-6-luna"
    assert ai_runtime.REASONING_EFFORT == "none"


def test_real_sdk_sends_one_responses_request_even_on_429(monkeypatch):
    requests = []
    monkeypatch.setattr(ai_runtime, "record_openai_usage", lambda **kw: None)
    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={"error": {"message": "rate limited", "type": "rate_limit_error"}})
    with openai.OpenAI(api_key="fake-not-a-secret", max_retries=0,
                       http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        with pytest.raises(ai_runtime.AiRateLimitError):
            ai_runtime._Messages(client).create(model="gpt-6-luna", max_tokens=512,
                messages=[{"role": "user", "content": "translate"}])
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert requests[0].url.path == "/v1/responses"
    assert payload["model"] == "gpt-6-luna"
    assert payload["reasoning"] == {"effort": "none"}
    assert payload["max_output_tokens"] == 4608
    assert payload["store"] is False


def test_incomplete_billed_response_is_recorded_but_never_returned(monkeypatch):
    recorded = []
    monkeypatch.setattr(ai_runtime, "record_openai_usage", lambda **kw: recorded.append(kw))
    response = SimpleNamespace(status="incomplete", output_text="partial", model="gpt-6-luna",
                               usage={"input_tokens": 10, "output_tokens": 4096})
    client = SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: response))
    with pytest.raises(ai_runtime.AiStatusError):
        ai_runtime._Messages(client).create(model="gpt-6-luna", max_tokens=256, messages=[])
    assert len(recorded) == 1 and recorded[0]["ok"] is False
    assert recorded[0]["usage"]["output_tokens"] == 4096


def test_openai_cached_and_reasoning_tokens_are_not_double_counted():
    tokens = api_usage.openai_usage_tokens({
        "input_tokens": 1000, "input_tokens_details": {"cached_tokens": 200},
        "output_tokens": 2000, "output_tokens_details": {"reasoning_tokens": 1900},
    })
    assert tokens == {"input_tokens": 1000, "cached_tokens": 200, "output_tokens": 2000}
    assert api_usage.openai_cost_micro_usd("gpt-6-luna", **tokens) == 1082
    assert api_usage.openai_cost_micro_usd("gpt-6-luna", input_tokens=300000, output_tokens=1000) == 60750
    assert api_usage.openai_cost_micro_usd("gpt-6-luna", input_tokens=1000, output_tokens=0,
                                         cache_write_tokens=1000) == 125
