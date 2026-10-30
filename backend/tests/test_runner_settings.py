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


# --- 화면이 그릴 재료 --------------------------------------------------------

def test_form_view_offers_the_numeric_fields_with_korean_labels():
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    assert fields["params.initial_capital"]["label"] == "투자 금액"
    assert fields["params.initial_capital"]["unit"] == "money"
    assert fields["params.rsi_period"]["value"] == 14
    assert fields["risk.stop_loss_pct"]["unit"] == "pct"


def test_form_view_leaves_out_the_structural_choices():
    """문자 칸은 값이 아니라 구조의 선택이다 — 바꾸면 사실상 다른 매크로다."""
    macro = _macro(rule_type="J",
                   params={"ma_type": "EMA", "fast_period": 10, "slow_period": 30,
                           "initial_capital": 1_000_000})
    paths = {f["path"] for f in rs.form_view(macro)}
    assert "params.fast_period" in paths and "params.slow_period" in paths
    assert "params.ma_type" not in paths


def test_form_view_still_offers_a_risk_setting_that_is_turned_off():
    macro = _macro(risk={"invest_ratio": 1.0})
    fields = {f["path"]: f for f in rs.form_view(macro)}
    assert fields["risk.stop_loss_pct"]["value"] is None
    assert fields["risk.stop_loss_pct"]["clearable"] is True
    assert fields["risk.invest_ratio"]["clearable"] is False, "투자 비율은 비울 수 없다"


def test_every_form_field_is_actually_accepted_by_apply_changes():
    """화면에 보이는데 서버가 거절하는 칸이 있으면 안 된다 — 두 벌이 아니라는 뜻."""
    macro = _macro()
    for field in rs.form_view(macro):
        section, key = field["path"].split(".", 1)
        # 비율은 0~1 로 묶여 있다 — 올리면 스키마가 막는다. 칸마다 '유효한 다른 값' 을 고른다.
        if field["unit"] == "ratio":
            value = 0.5
        elif field["value"] is None:
            value = 7
        else:
            value = float(field["value"]) + 1
        after = rs.apply_changes(macro, {section: {key: value}})
        assert rs.changed_fields(macro, after) == [field["path"]]


# --- 실행기가 직접 판단하는 세션 ---------------------------------------------
#
# 운영은 ``RUNNER_LOCAL_DECISIONS=true`` 다(render.yaml). 그 세션에서는 **진입·청산 판단을
# 실행기의 엔진이** 하고, 그 엔진은 시작할 때 받은 매크로로 세워진다. 서버의 macro_json 을
# 바꿔 봐야 실제 매매는 달라지지 않는다 — 조건을 바꿔 주겠다고 해 놓고 아무 일도 안 하는
# 것이 이 기능이 저지를 수 있는 가장 나쁜 일이다. 그래서 **실행기가 정말 따라오는 것만**
# 바꾸게 한다: 투자 금액과 안전망(실행기 local 모드도 이 둘은 heartbeat 로 받아 반영한다).

def test_a_local_decision_session_cannot_change_the_conditions():
    with pytest.raises(HTTPException) as err:
        rs.apply_changes(_macro(), {"params": {"entry_threshold": 20}}, local_decisions=True)
    assert err.value.status_code == 400
    assert "실행기가 직접 판단" in err.value.detail


def test_a_local_decision_session_can_still_change_money_and_safety():
    after = rs.apply_changes(_macro(), {"params": {"initial_capital": 2_000_000},
                                        "risk": {"stop_loss_pct": 1.5}}, local_decisions=True)
    assert after.params["initial_capital"] == 2_000_000
    assert after.risk.stop_loss_pct == 1.5


def test_the_form_only_offers_what_a_local_session_really_follows():
    paths = {f["path"] for f in rs.form_view(_macro(), local_decisions=True)}
    assert "params.initial_capital" in paths and "risk.stop_loss_pct" in paths
    assert "params.rsi_period" not in paths, "바꿀 수 없는 칸을 보여 주면 안 된다"


def test_a_server_decided_session_is_unchanged():
    assert rs.apply_changes(_macro(), {"params": {"entry_threshold": 20}}).params["entry_threshold"] == 20
    assert "params.rsi_period" in {f["path"] for f in rs.form_view(_macro())}


# --- 칸 설명 · 지금 포지션에 걸리는가 ----------------------------------------

def _every_numeric_key() -> set[str]:
    """엔진 스키마가 가진 모든 숫자 칸 — 방식이 하나 늘어도 이 시험이 따라간다."""
    import inspect

    from engine import schema as sc

    keys: set[str] = set()
    models = [obj for name, obj in vars(sc).items()
              if name.startswith("Params") and inspect.isclass(obj)
              and hasattr(obj, "model_fields")]
    models.append(sc.Risk)
    for model in models:
        for key, field in model.model_fields.items():
            text = str(field.annotation)
            if ("int" in text or "float" in text) and "bool" not in text and "Literal" not in text:
                keys.add(key)
    return keys


def test_every_offered_field_says_what_it_does():
    """이름만 보여 주면 '투자 비율' 이 진입 값인지 청산 값인지 알 수 없다."""
    missing = sorted(key for key in _every_numeric_key() if not rs._HELP.get(key))
    assert not missing, f"설명 없는 칸: {missing}"


def test_money_and_ratio_do_not_touch_the_held_position():
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    assert fields["params.initial_capital"]["affects"] == "next_entry"
    assert fields["risk.invest_ratio"]["affects"] == "next_entry"
    assert fields["risk.cooldown_minutes"]["affects"] == "next_entry"


def test_anything_that_could_close_the_position_asks_first():
    """빠뜨리면 묻지도 않고 지금 포지션의 청산 조건이 바뀐다 — 모르는 칸은 'position' 이다."""
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    for path in ("risk.stop_loss_pct", "risk.daily_max_loss_pct",
                 "risk.max_holding_hours", "params.exit_threshold"):
        assert fields[path]["affects"] == "position", path


def test_an_unknown_field_defaults_to_asking():
    """새 방식이 들어와도 기본값은 '묻는다' 여야 한다 — 안 물어서 팔리는 쪽이 더 나쁘다."""
    macro = _macro(rule_type="G", params={"initial_capital": 1_000_000,
                                          "bb_period": 20, "bb_std": 2.0})
    fields = {f["path"]: f for f in rs.form_view(macro)}
    assert fields["params.bb_period"]["affects"] == "position"


def test_the_entry_only_list_stays_small_and_deliberate():
    """여기 든 칸은 '안 묻는다' 는 뜻이다. 늘릴 때는 정말 청산과 무관한지 확인하고 늘린다."""
    assert rs.ENTRY_ONLY_KEYS == ("initial_capital", "invest_ratio", "cooldown_minutes")


# --- 단위 --------------------------------------------------------------------

def test_money_reads_in_the_currency_the_exchange_uses():
    """바이낸스는 USDT, 업비트는 KRW — 매크로가 든 값을 쓴다. 화면이 짐작하면 어긋난다."""
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    assert fields["params.initial_capital"]["unit_text"] == "USDT"
    won = _macro(exchange="upbit", symbol="KRW-BTC", market="spot", leverage=1)
    fields = {f["path"]: f for f in rs.form_view(won)}
    assert fields["params.initial_capital"]["unit_text"] == "KRW"


def test_each_kind_of_number_carries_its_own_unit():
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    assert fields["risk.stop_loss_pct"]["unit_text"] == "%"
    assert fields["risk.max_holding_hours"]["unit_text"] == "시간"
    assert fields["risk.cooldown_minutes"]["unit_text"] == "분"
    assert fields["params.rsi_period"]["unit_text"] == "봉"


def test_a_threshold_without_a_unit_shows_none():
    """RSI 문턱은 단위가 없다 — 없는 단위를 지어내지 않는다."""
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    assert fields["params.entry_threshold"]["unit_text"] == ""


def test_invest_ratio_shows_no_unit_because_percent_would_be_a_lie():
    """0~1 로 저장한다 — "1" 옆에 "%" 를 붙이면 1% 로 읽힌다. 설명이 대신 말한다."""
    fields = {f["path"]: f for f in rs.form_view(_macro())}
    assert fields["risk.invest_ratio"]["unit_text"] == ""
    assert "1 = 전부" in fields["risk.invest_ratio"]["help"]


def test_the_record_summary_carries_units_too():
    """증빙은 "손절 3 → 2" 가 아니라 "손절 3% → 2%" 여야 읽힌다."""
    before = _macro()
    after = rs.apply_changes(before, {"risk": {"stop_loss_pct": 2.0},
                                      "params": {"initial_capital": 2_000_000}})
    text = rs.summary(before, after)
    assert "2%" in text and "USDT" in text


def test_a_setting_turned_off_reads_without_a_unit():
    before = _macro()
    after = rs.apply_changes(before, {"risk": {"stop_loss_pct": None}})
    assert "없음" in rs.summary(before, after)
    assert "없음%" not in rs.summary(before, after)
