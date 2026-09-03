"""AI 원인 분석 층 (Anthropic Claude) — server key gating, fallback, parsing.

No real network calls: the anthropic client is monkeypatched. The key is read
from the server env ANTHROPIC_API_KEY (no user-supplied key).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import ai_explain, ai_runtime
from app.agent_features.position_news import repository
from app.ai_explain import AiError
from app.engine.backtest import BacktestResult
from app.engine.explain import Explanation
from app.engine.schema import Macro
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def _reset_ai_runtime(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(ai_explain, "_daily_budget", ("", 0), raising=False)
    ai_runtime.close_ai_runtime()
    yield
    ai_runtime.close_ai_runtime()


def _macro():
    return Macro(symbol="BTCUSDT", rule_type="A", candle_interval="1d",
                 params=dict(take_profit_pct=5, initial_capital=1_000_000),
                 risk={"stop_loss_pct": 3})


def _result():
    return BacktestResult(
        final_return_pct=12.0, win_rate_pct=60.0, mdd_pct=9.0, total_trades=10,
        initial_capital=1_000_000.0, final_equity=1_120_000.0, equity_curve=[],
        buy_hold_return_pct=5.0,
    )


class _Block:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Resp:
    def __init__(self, text):
        # a thinking block may precede the text block on some models
        self.content = [_Block(text)]


class _Messages:
    def __init__(self, outcome, capture=None):
        self._outcome = outcome
        self._capture = capture

    def create(self, **kwargs):
        if self._capture is not None:
            self._capture.update(kwargs)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return _Resp(self._outcome)


class _FakeClient:
    def __init__(self, outcome, capture=None):
        self.messages = _Messages(outcome, capture)


def _patch_anthropic(monkeypatch, outcome, capture=None):
    monkeypatch.setattr(ai_explain.anthropic, "Anthropic",
                        lambda *a, **k: _FakeClient(outcome, capture))


def _good_json():
    return json.dumps({
        "mood": "win",
        "headline": "익절 목표가 촘촘해 추세를 놓쳤어",
        "points": ["승률 60%지만 홀딩보다 앞섰어", "MDD 9%로 낙폭은 얕았어"],
    }, ensure_ascii=False)


def test_no_key_returns_rule_based(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ai_explain.ai_available() is False
    out = ai_explain.enrich(_macro(), _result())
    assert isinstance(out, Explanation)
    assert out.source == "rule"


def test_server_key_generates_ai(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    captured = {}
    _patch_anthropic(monkeypatch, _good_json(), captured)
    out = ai_explain.generate(_macro(), _result())
    assert out.source == "ai"
    assert out.mood == "win"
    assert len(out.points) == 2
    assert captured["model"]  # a model id was passed


def test_enrich_uses_env_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _patch_anthropic(monkeypatch, _good_json())
    assert ai_explain.enrich(_macro(), _result()).source == "ai"


def test_code_fenced_json_is_parsed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    fenced = "```json\n" + _good_json() + "\n```"
    _patch_anthropic(monkeypatch, fenced)
    assert ai_explain.generate(_macro(), _result()).source == "ai"


def test_points_capped_at_five(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    many = json.dumps({"mood": "win", "headline": "원인", "points": [f"p{i}" for i in range(8)]})
    _patch_anthropic(monkeypatch, many)
    assert len(ai_explain.generate(_macro(), _result()).points) == 5


def test_incomplete_reply_raises_aierror(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    bad = json.dumps({"mood": "win", "headline": "", "points": []})
    _patch_anthropic(monkeypatch, bad)
    with pytest.raises(AiError):
        ai_explain.generate(_macro(), _result())


def test_generic_failure_falls_back_in_enrich(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    _patch_anthropic(monkeypatch, RuntimeError("boom"))
    assert ai_explain.enrich(_macro(), _result()).source == "rule"


def test_ai_explain_daily_budget_caps_each_process(monkeypatch):
    reserve = getattr(ai_explain, "_reserve_ai_explain_call", None)
    assert reserve is not None
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(ai_explain, "_MAX_CALLS_PER_DAY", 2)
    monkeypatch.setattr(ai_explain, "_daily_budget", ("", 0))
    monkeypatch.setattr(ai_explain, "_kst_date", lambda: "2026-09-03")

    assert reserve() is True
    assert reserve() is True
    assert reserve() is False
    assert ai_explain._daily_budget == ("2026-09-03", 2)


def test_ai_explain_budget_uses_durable_namespace_with_postgres(monkeypatch):
    calls = []
    reserve = getattr(ai_explain, "_reserve_ai_explain_call", None)
    assert reserve is not None
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setattr(ai_explain, "_MAX_CALLS_PER_DAY", 7)
    monkeypatch.setattr(
        ai_explain,
        "_reserve_durable_ai_explain_budget",
        lambda *, daily_limit: calls.append(daily_limit) or True,
        raising=False,
    )

    assert reserve() is True
    assert calls == [7]


def test_durable_ai_explain_budget_uses_its_own_namespace(monkeypatch):
    calls = []

    def reserve_ai_budget(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(repository, "reserve_ai_budget", reserve_ai_budget)

    assert ai_explain._reserve_durable_ai_explain_budget(daily_limit=7) is True
    assert calls == [{"daily_limit": 7, "namespace": "ai_explain"}]


def test_ai_explain_budget_fails_closed_when_shared_database_is_unavailable(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example.invalid/db")
    monkeypatch.setattr(
        ai_explain,
        "_reserve_durable_ai_explain_budget",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("db down")),
    )

    assert ai_explain._reserve_ai_explain_call() is False


def test_ai_explain_budget_is_charged_only_for_uncached_api_load(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    reservations = []
    monkeypatch.setattr(
        ai_explain,
        "_reserve_ai_explain_call",
        lambda: reservations.append(True) or True,
        raising=False,
    )
    _patch_anthropic(monkeypatch, _good_json())

    first, first_state = ai_explain.generate_with_cache_status(_macro(), _result())
    second, second_state = ai_explain.generate_with_cache_status(_macro(), _result())

    assert first.source == second.source == "ai"
    assert (first_state, second_state) == ("loaded", "cached")
    assert reservations == [True]


def test_ai_explain_rejects_uncached_call_when_daily_budget_is_exhausted(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(
        ai_explain,
        "_reserve_ai_explain_call",
        lambda: False,
        raising=False,
    )
    monkeypatch.setattr(
        ai_explain,
        "get_anthropic_client",
        lambda: (_ for _ in ()).throw(AssertionError("Anthropic must not run")),
    )

    with pytest.raises(AiError, match="사용할 수 있는 AI 심화 분석 횟수"):
        ai_explain.generate(_macro(), _result())


def test_endpoint_reports_ai_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    body = {
        "macro": {
            "symbol": "BTCUSDT", "rule_type": "A", "candle_interval": "1d",
            "params": {"take_profit_pct": 5, "initial_capital": 1_000_000},
            "risk": {"stop_loss_pct": 3}, "period": {"preset": "3m"},
        }
    }
    res = client.post("/api/explain/ai", json=body)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["ai_available"] is False
    assert data["explanation"]["source"] == "rule"
