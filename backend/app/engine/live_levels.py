"""돌고 있는 세션의 '지금 어디서 사고 어디서 파는가' 를 가격선으로 낸다.

왜 서버에 있나: 화면이 직접 계산하면 규칙 산수가 두 벌이 된다. 실제로 그 일이 있었다 —
E 눌림목의 '진입 목표 -3%' 선을 화면이 **현재가** 기준으로 그려서 가격을 따라 영원히
내려왔고(엔진은 평평해진 순간 잡힌 고정 기준가를 본다), 사용자는 "안 사진다" 로 읽었다.
그래서 선은 엔진 상태에서만 나온다. 모르면 ``None`` — 추측해서 그리면 거짓이 된다.

입력은 체크포인트의 전략 상태(driver.state() 의 legs[].strategy)와 평단뿐이다.
이 모듈은 DB·HTTP·시세를 모른다.
"""
from __future__ import annotations

from typing import Optional

from .schema import Macro, RuleType

EMPTY = {"entry_target": None, "trail_stop": None, "trail_activation": None, "armed": False}


def _num(value) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def strategy_levels(macro: Macro, state: Optional[dict], entry_price: float) -> dict:
    """E(트레일링 스탑) 세션의 가격선. 다른 규칙은 아직 빈 칸을 낸다.

    - ``entry_target``   무포지션 · 눌림목 대기일 때 고정 기준가의 -entry_dip%
    - ``trail_activation`` 보유 중 · 발동 전일 때 평단의 +activation_profit%
    - ``trail_stop``     보유 중 · 발동 뒤일 때 고점의 -trail_percent%
    """
    if macro.rule_type is not RuleType.E or not state:
        return dict(EMPTY)

    params = macro.params or {}
    held = _num(entry_price) is not None
    out = dict(EMPTY)
    # armed·peak 는 포지션이 있어야 뜻이 있다. 청산 뒤에도 심에 남아 있지만(_reset_position_state
    # 는 웜업에서만 불리고, 다음 진입이 _peak·_armed 를 덮는다) 평평한 세션에 "발동됨" 을
    # 내보내면 화면이 거짓을 말한다.
    out["armed"] = bool(state.get("armed")) if held else False

    if not held:
        if str(params.get("entry_mode", "immediate")) != "dip":
            return out
        ref = _num(state.get("dip_ref"))
        dip = params.get("entry_dip")
        if ref is not None and dip is not None:
            out["entry_target"] = ref * (1 - float(dip) / 100.0)
        return out

    if out["armed"]:
        peak = _num(state.get("peak"))
        trail = params.get("trail_percent")
        if peak is not None and trail is not None:
            out["trail_stop"] = peak * (1 - float(trail) / 100.0)
    else:
        activation = params.get("activation_profit")
        if activation is not None:
            out["trail_activation"] = float(entry_price) * (1 + float(activation) / 100.0)
    return out
