"""일일 챌린지 프롬프트는 스키마가 요구하는 params 이름을 직접 알려줘야 한다.

프롬프트가 params 를 '{...}' 로 생략한 채 "타입에 맞게 채워" 라고만 해서, 모델이
필드 이름을 추측했고 스키마 검증을 전부 통과하지 못했다. 실패분은 템플릿으로
조용히 채워져 겉으로는 정상이었다 — 챌린지가 줄곧 템플릿만 쓰고 있었다.
"""
from __future__ import annotations

import logging

import pytest

pytest.importorskip("pydantic")

from app import ai_challenge
from app.engine.schema import Macro, RuleType, required_param_names

# 프롬프트가 모델에게 허용한 타입들.
OFFERED = ("A", "E", "F", "J")

# 각 타입을 '프롬프트가 알려준 이름만으로' 채운 값. 이름이 틀리면 Macro 가 거부한다.
ADVERTISED_VALUES = {
    "take_profit_pct": 5, "initial_capital": 1_000_000, "trail_percent": 3,
    "fast_period": 20, "slow_period": 60,
}


def test_schema_exposes_required_param_names_per_rule_type():
    """필수 필드 목록은 스키마가 내준다 — 프롬프트가 손으로 베끼면 또 어긋난다."""
    assert required_param_names(RuleType.A) == ("take_profit_pct", "initial_capital")
    assert required_param_names(RuleType.E) == ("trail_percent", "initial_capital")
    assert required_param_names(RuleType.F) == ("initial_capital",)
    assert required_param_names(RuleType.J) == ("fast_period", "slow_period", "initial_capital")


def test_system_prompt_names_every_required_param_of_every_offered_type():
    """스키마에 필수 필드가 새로 생기면 이 시험이 먼저 깨진다."""
    for code in OFFERED:
        for name in required_param_names(RuleType(code)):
            assert name in ai_challenge._SYSTEM, f"프롬프트에 {code} 의 {name} 이 없다"


def test_a_macro_made_only_from_the_advertised_names_validates():
    """프롬프트가 알려준 이름만으로 채운 매크로는 반드시 검증을 통과해야 한다."""
    for code in OFFERED:
        params = {name: ADVERTISED_VALUES[name] for name in required_param_names(RuleType(code))}
        macro = {"rule_type": code, "candle_interval": "1h", "params": params,
                 "risk": {"stop_loss_pct": 3}, "position_side": "long"}
        assert ai_challenge._valid(macro, "BTCUSDT") is not None, f"{code} 가 검증을 통과하지 못한다"


def test_prompt_states_the_fast_slow_order_rule():
    """J 는 fast_period < slow_period 라는 교차 조건이 있다 — 모르면 절반이 떨어진다."""
    assert "fast_period" in ai_challenge._SYSTEM and "slow_period" in ai_challenge._SYSTEM
    assert ai_challenge._valid(
        {"rule_type": "J", "candle_interval": "1h",
         "params": {"fast_period": 60, "slow_period": 20, "initial_capital": 1_000_000}},
        "BTCUSDT") is None, "역순은 스키마가 거부한다 — 프롬프트가 순서를 알려야 한다"
    assert "<" in ai_challenge._SYSTEM, "프롬프트가 fast < slow 를 명시해야 한다"


def test_dropped_ai_proposals_are_logged(monkeypatch, caplog):
    """전부 떨어져도 흔적이 없었다 — 폴백이 조용히 가려 줬다."""
    monkeypatch.setattr(ai_challenge, "ai_available", lambda: True)
    monkeypatch.setattr(ai_challenge, "_ai_propose",
                        lambda symbol: [{"rule_type": "A", "params": {"tp_pct": 5}}])

    with caplog.at_level(logging.WARNING, logger="app.ai_challenge"):
        macros = ai_challenge.generate_macros("BTCUSDT", 3)

    assert len(macros) == 3, "폴백은 그대로 유지한다"
    messages = [record.getMessage() for record in caplog.records]
    assert any("1" in message and "0" in message for message in messages), messages
    assert any("challenge" in message.lower() for message in messages), messages
