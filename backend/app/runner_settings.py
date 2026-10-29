"""구동 중인 매크로의 설정을 바꾼다 — 무엇을 바꿀 수 있는지 한 곳에서 정한다.

설계: docs/superpowers/specs/2026-10-30-runner-live-settings-design.md

바꿀 수 있는 것은 **금액 · 방식별 조건 값 · 위험 설정** 뿐이다(``params`` 와 ``risk``).
나머지는 매크로의 **정체**다 — 매매 방식 · 종목 · 거래소 · 시장 · 방향 · 레버리지 ·
봉 간격을 바꾸면 들고 있는 포지션과 어긋난다. 그건 다른 매크로이므로 종료하고 새로
시작하는 게 맞다.

이 모듈은 DB 도 HTTP 도 모른다 — 매크로 하나를 받아 새 매크로 하나를 돌려준다.
적립(기록) · 전략 재시작 · 실행기 전달은 runner.py 가 맡는다.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException

from engine import Macro

# 바꿀 수 있는 칸. 매크로의 다른 모든 칸은 '정체' 라 손대지 않는다.
EDITABLE_SECTIONS = ("params", "risk")

# 손댈 수 없는 칸을 왜 못 바꾸는지 — 사용자가 읽을 말. 이름을 그대로 보여 주면
# "candle_interval 은 바꿀 수 없어요" 가 되어 무슨 말인지 모른다.
_FIELD_LABELS = {
    "rule_type": "매매 방식", "symbol": "종목", "symbols": "종목", "legs": "종목 구성",
    "exchange": "거래소", "market": "시장", "position_side": "방향(롱·숏)",
    "leverage": "레버리지", "candle_interval": "봉 간격", "period": "기간",
    "fees": "수수료", "margin_mode": "증거금 방식", "quote_currency": "거래 통화",
    "entry_filter": "진입 필터", "bundle_risk": "묶음 한도",
}
_IDENTITY_NOTE = "구동 중에는 바꿀 수 없어요 — 종료하고 새로 시작해 주세요."


def _label(name: str) -> str:
    return _FIELD_LABELS.get(name, name)


def _merge(base: dict, patch: dict) -> dict:
    """``patch`` 의 칸만 덮어쓴다. 값이 ``None`` 이면 **비우는 것**이다(손절 끄기 등).

    안 보낸 칸은 그대로 둔다 — 화면은 바꾼 칸만 보내고, 전체를 보내게 하면 화면이
    들고 있던 묵은 값이 조용히 되살아난다.
    """
    out = dict(base)
    out.update(patch)
    return out


def apply_changes(macro: Macro, changes: dict[str, Any]) -> Macro:
    """바뀐 칸만 덮어쓴 새 매크로. 손댈 수 없는 칸이 오거나 바뀐 게 없으면 거절한다.

    스키마가 한 번 더 본다 — 마틴게일 최악의 경우가 자금을 넘거나, 숏 A 의 손절을 끄는
    변경은 ``Macro`` 가 거절하고 그 이유를 그대로 사용자에게 보여 준다.
    """
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(status_code=400, detail="바꿀 내용을 보내 주세요.")
    refused = [key for key in changes if key not in EDITABLE_SECTIONS]
    if refused:
        names = " · ".join(_label(key) for key in refused)
        raise HTTPException(status_code=400, detail=f"{names} 은(는) {_IDENTITY_NOTE}")

    body = macro.model_dump(mode="json")
    for section in EDITABLE_SECTIONS:
        patch = changes.get(section)
        if patch is None:
            continue
        if not isinstance(patch, dict):
            raise HTTPException(status_code=400, detail=f"{section} 은(는) 칸 이름과 값으로 보내 주세요.")
        body[section] = _merge(body.get(section) or {}, patch)

    try:
        after = Macro(**body)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_reason(exc)) from exc

    if not changed_fields(macro, after):
        raise HTTPException(status_code=400, detail="바뀐 값이 없어요.")
    return after


def _reason(exc: Exception) -> str:
    """스키마가 거절한 이유 한 줄. 사용자가 고칠 수 있어야 하므로 비워 두지 않는다."""
    text = str(exc).strip().replace("\n", " ")
    # pydantic 은 여러 줄로 길게 쓴다 — 첫 문장만 쓰고, 그래도 비면 일반 문구로.
    return (text[:300] or "이 값으로는 매크로를 만들 수 없어요. 값을 확인해 주세요.")


def _section(macro: Macro, name: str) -> dict:
    value = getattr(macro, name, None)
    if value is None:
        return {}
    return value if isinstance(value, dict) else value.model_dump(mode="json")


def changed_fields(before: Macro, after: Macro) -> list[str]:
    """바뀐 칸 이름들(``params.rsi_period`` 꼴). 바뀐 게 없으면 빈 목록.

    기록(증빙)과 화면이 같은 목록을 쓴다 — 두 벌로 세면 "무엇을 바꿨다" 가 서로 달라진다.
    """
    out: list[str] = []
    for section in EDITABLE_SECTIONS:
        old, new = _section(before, section), _section(after, section)
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key):
                out.append(f"{section}.{key}")
    return out


_VALUE_LABELS = {
    "initial_capital": "투자 금액", "invest_ratio": "투자 비율", "stop_loss_pct": "손절",
    "daily_max_loss_pct": "일일 손실 한도", "max_holding_hours": "최대 보유시간",
    "cooldown_minutes": "재진입 대기", "take_profit_pct": "익절", "take_profit": "익절",
    "rsi_period": "RSI 기간", "entry_threshold": "진입 기준", "exit_threshold": "청산 기준",
    "fast_period": "단기 이동평균", "slow_period": "장기 이동평균", "ma_type": "이동평균 종류",
    "bb_period": "볼린저 기간", "bb_std": "볼린저 폭", "trail_percent": "트레일링 폭",
    "activation_profit": "트레일링 시작", "k": "돌파 계수", "interval_days": "매수 주기",
    "amount_per_buy": "한 번에 살 금액", "base_order_size": "기본 주문",
    "safety_order_size": "세이프티 주문", "max_safety_orders": "세이프티 횟수",
    "price_deviation": "추가 매수 간격",
}


def _show(value: Any) -> str:
    if value is None:
        return "없음"
    if isinstance(value, bool):
        return "켬" if value else "끔"
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer() and abs(number) >= 1000:
            return f"{int(number):,}"
        return f"{number:g}"
    return str(value)


def summary(before: Macro, after: Macro) -> str:
    """사람이 읽는 변경 요약 — "투자 금액 1,000,000 → 2,000,000" 꼴.

    기록 한 줄과 화면·알림이 같은 문장을 쓴다. 칸 이름을 그대로 보여 주지 않는다
    (``initial_capital`` 은 사용자의 말이 아니다).
    """
    parts: list[str] = []
    for path in changed_fields(before, after):
        section, key = path.split(".", 1)
        old = _section(before, section).get(key)
        new = _section(after, section).get(key)
        parts.append(f"{_VALUE_LABELS.get(key, key)} {_show(old)} → {_show(new)}")
    return " · ".join(parts)


def editable_view(macro: Macro) -> dict:
    """화면이 수정 칸을 그릴 때 쓰는 현재 값 — 바꿀 수 있는 것만 추려서."""
    return {section: _section(macro, section) for section in EDITABLE_SECTIONS}
