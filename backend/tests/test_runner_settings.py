"""구동 중 매크로 설정 변경 — 무엇을 바꿀 수 있고 무엇을 거절하는가 (2026-10-30).

설계 문서: docs/superpowers/specs/2026-10-30-runner-live-settings-design.md

바꿀 수 있는 것은 **금액 · 방식별 조건 값 · 위험 설정** 뿐이다(``params`` 와 ``risk``).
매매 방식 · 종목 · 거래소 · 시장 · 방향 · 레버리지 · 봉 간격은 바꾸면 **다른 매크로**가
되어 들고 있는 포지션과 어긋난다 — 종료하고 새로 시작해야 한다.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import runner_settings as rs
from engine import Macro


def _macro(**over) -> Macro:
    body = {
        "symbol": "BTCUSDT", "rule_type": "F", "position_side": "long",
        "candle_interval": "1h", "market": "futures", "leverage": 2,
        "params": {"rsi_period": 14, "entry_threshold": 30, "exit_threshold": 70,
                   "initial_capital": 1_000_000},
        "risk": {"invest_ratio": 1.0, "stop_loss_pct": 3.0},
        "period": {"preset": "3m"},
    }
    body.update(over)
    return Macro(**body)


def _apply(macro: Macro, changes: dict) -> Macro:
    return rs.apply_changes(macro, changes)


# --- 바꿀 수 있는 것 ---------------------------------------------------------

def test_amount_can_change():
    after = _apply(_macro(), {"params": {"initial_capital": 2_000_000}})
    assert after.params["initial_capital"] == 2_000_000


def test_condition_values_can_change_and_untouched_ones_stay():
    after = _apply(_macro(), {"params": {"entry_threshold": 25}})
    assert after.params["entry_threshold"] == 25
    assert after.params["rsi_period"] == 14, "안 보낸 칸은 그대로 둔다 — 부분 수정이다"
    assert after.params["initial_capital"] == 1_000_000


def test_risk_settings_can_change():
    after = _apply(_macro(), {"risk": {"stop_loss_pct": 1.5, "max_holding_hours": 12}})
    assert after.risk.stop_loss_pct == 1.5
    assert after.risk.max_holding_hours == 12
    assert after.risk.invest_ratio == 1.0


def test_an_explicit_null_clears_a_risk_setting():
    # 손절을 끄는 길. 안 보내는 것(그대로 두기)과 구분되어야 한다.
    after = _apply(_macro(), {"risk": {"stop_loss_pct": None}})
    assert after.risk.stop_loss_pct is None


def test_everything_else_on_the_macro_is_untouched():
    after = _apply(_macro(), {"params": {"entry_threshold": 25}})
    before = _macro()
    for field in ("symbol", "rule_type", "position_side", "candle_interval", "market", "leverage"):
        assert getattr(after, field) == getattr(before, field)


# --- 거절하는 것 -------------------------------------------------------------

@pytest.mark.parametrize("changes", [
    {"rule_type": "J"},
    {"symbol": "ETHUSDT"},
    {"exchange": "upbit"},
    {"market": "spot"},
    {"position_side": "short"},
    {"leverage": 3},
    {"candle_interval": "4h"},
    {"period": {"preset": "1y"}},
    {"fees": {"commission_pct": 0.0}},
    {"symbols": ["BTCUSDT", "ETHUSDT"]},
    {"legs": []},
])
def test_identity_fields_are_refused(changes):
    """이걸 바꾸면 들고 있는 포지션과 어긋난다 — 다른 매크로이기 때문이다."""
    with pytest.raises(HTTPException) as err:
        _apply(_macro(), changes)
    assert err.value.status_code == 400
    assert "바꿀 수 없어요" in err.value.detail


def test_unknown_sections_are_refused():
    # 모르는 칸을 조용히 흘리면 "바꿨다" 는 기록과 실제가 어긋난다.
    with pytest.raises(HTTPException):
        _apply(_macro(), {"뭔가": {"x": 1}})


def test_a_change_that_breaks_the_schema_is_refused_with_its_reason():
    # 숏 A 는 손절이 필수다(숏 손실은 이론상 무한). 스키마의 말을 그대로 사용자에게 보여 준다.
    macro = _macro(rule_type="A", position_side="short",
                   params={"take_profit_pct": 3, "initial_capital": 1_000_000},
                   risk={"invest_ratio": 1.0, "stop_loss_pct": 2.0})
    with pytest.raises(HTTPException) as err:
        _apply(macro, {"risk": {"stop_loss_pct": None}})
    assert err.value.status_code == 400
    assert err.value.detail, "이유 없는 거절은 사용자가 고칠 수 없다"


def test_a_change_that_changes_nothing_is_refused():
    with pytest.raises(HTTPException) as err:
        _apply(_macro(), {"params": {"rsi_period": 14}})
    assert "바뀐" in err.value.detail


def test_empty_changes_are_refused():
    with pytest.raises(HTTPException):
        _apply(_macro(), {})


# --- 무엇이 바뀌었나 ---------------------------------------------------------

def test_changed_fields_names_every_changed_value():
    before = _macro()
    after = _apply(before, {"params": {"entry_threshold": 25, "initial_capital": 2_000_000},
                            "risk": {"stop_loss_pct": 1.5}})
    assert rs.changed_fields(before, after) == [
        "params.entry_threshold", "params.initial_capital", "risk.stop_loss_pct"]


def test_changed_fields_is_empty_for_the_same_macro():
    assert rs.changed_fields(_macro(), _macro()) == []


def test_changed_fields_sees_a_cleared_setting():
    before = _macro()
    after = _apply(before, {"risk": {"stop_loss_pct": None}})
    assert rs.changed_fields(before, after) == ["risk.stop_loss_pct"]


# --- 사람이 읽는 요약 --------------------------------------------------------

def test_the_summary_reads_in_korean_and_shows_both_sides():
    before = _macro()
    after = _apply(before, {"params": {"initial_capital": 2_000_000}})
    text = rs.summary(before, after)
    assert "1,000,000" in text and "2,000,000" in text
    assert "→" in text
