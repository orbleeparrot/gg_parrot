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

# 실행기가 **직접 판단하는** 세션(decides_locally)에서 바꿀 수 있는 ``params`` 칸.
#
# 운영은 ``RUNNER_LOCAL_DECISIONS=true`` 다(render.yaml). 그 세션에서는 진입·청산 판단을
# 실행기의 엔진이 하고, 그 엔진은 **시작할 때 받은 매크로**로 세워진다. 서버의 macro_json
# 을 바꾸고 서버 전략을 다시 세워도 실제 매매는 달라지지 않는다.
#
# 조건을 바꿔 주겠다고 해 놓고 아무 일도 하지 않는 것이 이 기능이 저지를 수 있는 가장
# 나쁜 일이다. 그래서 **실행기가 정말 따라오는 것만** 바꾸게 한다 — 투자 금액과 안전망은
# local 모드에서도 heartbeat 의 settings 로 받아 반영한다(runner-core/src/bot/settings.rs).
LOCAL_EDITABLE_PARAMS = ("initial_capital",)
LOCAL_ONLY_NOTE = (
    "이 실행은 실행기가 직접 판단해요. 투자 금액과 위험 설정만 바꿀 수 있어요 — "
    "매매 조건을 바꾸려면 종료하고 새로 시작해 주세요.")

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


def apply_changes(macro: Macro, changes: dict[str, Any], *, local_decisions: bool = False) -> Macro:
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
        if local_decisions and section == "params":
            blocked = [key for key in patch if key not in LOCAL_EDITABLE_PARAMS]
            if blocked:
                raise HTTPException(status_code=400, detail=LOCAL_ONLY_NOTE)
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
    quote = str(getattr(after, "quote_currency", "") or "")
    parts: list[str] = []
    for path in changed_fields(before, after):
        section, key = path.split(".", 1)
        old = _section(before, section).get(key)
        new = _section(after, section).get(key)
        unit = _unit_text(key, _UNITS.get(key, ""), quote)
        parts.append(f"{_VALUE_LABELS.get(key, key)} "
                     f"{_with_unit(old, unit)} → {_with_unit(new, unit)}")
    return " · ".join(parts)


def _with_unit(value: Any, unit: str) -> str:
    """값에 단위를 붙인 글자. 끈 칸("없음")에는 붙이지 않는다 — "없음 %" 는 말이 안 된다.

    통화 기호(USDT · KRW)만 띄어 쓴다. 한국어 단위는 붙여 쓰는 말이다 — "12 시간" 이
    아니라 "12시간".
    """
    text = _show(value)
    if not unit or value is None:
        return text
    return f"{text} {unit}" if unit.isascii() and unit.isalpha() else f"{text}{unit}"


def editable_view(macro: Macro) -> dict:
    """화면이 수정 칸을 그릴 때 쓰는 현재 값 — 바꿀 수 있는 것만 추려서."""
    return {section: _section(macro, section) for section in EDITABLE_SECTIONS}


# 칸 이름 → 단위. 화면이 "3%" 와 "1,000,000 USDT" 를 다르게 그린다.
_UNITS = {
    "initial_capital": "money", "amount_per_buy": "money", "base_order_size": "money",
    "safety_order_size": "money", "per_grid_invest": "money",
    "invest_ratio": "ratio",
    "stop_loss_pct": "pct", "daily_max_loss_pct": "pct", "take_profit_pct": "pct",
    "take_profit": "pct", "price_deviation": "pct", "trail_percent": "pct",
    "activation_profit": "pct", "entry_dip": "pct", "long_take_profit_pct": "pct",
    "max_holding_hours": "hours", "cooldown_minutes": "minutes", "interval_days": "days",
    "lower_price": "money", "upper_price": "money",
}

# 입력 칸 옆에 붙일 단위. 숫자만 있으면 "2" 가 2%인지 2시간인지 2봉인지 알 수 없다.
#
# 돈은 거래소가 정한다 — 업비트는 KRW, 바이낸스는 USDT. 매크로가 이미 들고 있는 값을
# 그대로 쓴다(``quote_currency``). 화면이 거래소를 보고 짐작하면 두 벌이 되어 어긋난다.
_UNIT_TEXT = {"pct": "%", "hours": "시간", "minutes": "분", "days": "일"}

# 단위 칸에 안 걸리는 숫자들의 단위. 봉 수 · 횟수 · 배수는 돈도 퍼센트도 아니다.
_UNIT_TEXT_BY_KEY = {
    "rsi_period": "봉", "bb_period": "봉", "fast_period": "봉", "slow_period": "봉",
    "squeeze_lookback": "봉", "ma_filter_period": "봉", "confirm_candles": "봉",
    "grid_count": "칸", "max_safety_orders": "회", "session_start_hour": "시",
    "bb_std": "배", "k": "배",
    "safety_order_step_scale": "배", "safety_order_volume_scale": "배",
}


def _unit_text(key: str, unit: str, quote: str) -> str:
    """이 칸의 단위 글자. 없으면 빈 글자 — RSI 문턱처럼 단위가 없는 값도 있다.

    투자 비율은 일부러 비운다. 0~1 로 저장하므로 "1" 옆에 "%" 를 붙이면 1% 로 읽힌다 —
    틀린 단위를 보여 주느니 설명 한 줄로 "1 = 전부" 라고 말하는 편이 낫다.
    """
    if unit == "money":
        return quote or "USDT"
    return _UNIT_TEXT.get(unit) or _UNIT_TEXT_BY_KEY.get(key, "")


# 칸이 **무슨 일을 하는지** 한 줄. 화면이 칸 이름만 보여 주면 "투자 비율" 이 진입 때 쓰는
# 값인지 들고 있는 것에도 걸리는 값인지 알 수 없다 — 그걸 모르고 바꾸는 게 제일 위험하다.
_HELP = {
    "initial_capital": "다음에 살 때 쓸 금액이에요. 지금 들고 있는 건 그대로예요.",
    "invest_ratio": "한 번에 투자 금액의 얼마를 쓸지예요(1 = 전부). 다음에 살 때부터 적용돼요.",
    "stop_loss_pct": "이만큼 손해 나면 팔아요.",
    "take_profit_pct": "이만큼 이익 나면 팔아요.",
    "take_profit": "이만큼 이익 나면 팔아요.",
    "long_take_profit_pct": "롱 쪽이 이만큼 이익 나면 팔아요.",
    "daily_max_loss_pct": "하루에 이만큼 잃으면 그날은 더 사지 않아요.",
    "max_holding_hours": "이 시간이 넘으면 들고 있던 걸 정리해요.",
    "cooldown_minutes": "판 뒤 이만큼은 다시 사지 않아요.",
    "trail_percent": "고점에서 이만큼 내려오면 팔아요.",
    "activation_profit": "이만큼 이익이 난 뒤부터 고점을 따라가요.",
    "rsi_period": "RSI 를 몇 개 봉으로 계산할지예요.",
    "entry_threshold": "RSI 가 이 값을 지나면 사요.",
    "exit_threshold": "RSI 가 이 값을 지나면 팔아요.",
    "fast_period": "짧은 쪽 이동평균의 봉 수예요.",
    "slow_period": "긴 쪽 이동평균의 봉 수예요.",
    "bb_period": "볼린저 밴드를 몇 개 봉으로 그릴지예요.",
    "bb_std": "밴드를 얼마나 넓게 잡을지예요.",
    "k": "전날 폭의 몇 배를 넘으면 살지예요.",
    "interval_days": "며칠에 한 번 살지예요.",
    "amount_per_buy": "한 번에 살 금액이에요.",
    "base_order_size": "맨 처음 사는 금액이에요.",
    "safety_order_size": "떨어졌을 때 더 사는 금액이에요.",
    "max_safety_orders": "떨어졌을 때 몇 번까지 더 살지예요.",
    "price_deviation": "이만큼 더 떨어질 때마다 더 사요.",
    "entry_dip": "이만큼 떨어졌을 때 사요.",
    "per_grid_invest": "칸 하나에 넣을 금액이에요.",
    "confirm_candles": "신호가 이만큼 연달아 나와야 움직여요.",
    "squeeze_lookback": "밴드가 좁은지 볼 때 몇 개 봉을 돌아볼지예요.",
    "ma_filter_period": "이 이동평균 위에 있을 때만 사요. 비우면 안 봐요.",
    "session_start_hour": "하루를 이 시각부터 세요.",
    "lower_price": "격자의 맨 아래 가격이에요.",
    "upper_price": "격자의 맨 위 가격이에요.",
    "grid_count": "아래위 사이를 몇 칸으로 나눌지예요.",
    "safety_order_step_scale": "추가 매수 간격을 회차마다 몇 배로 벌릴지예요.",
    "safety_order_volume_scale": "추가 매수 금액을 회차마다 몇 배로 키울지예요.",
    "drop_trigger_pct": "평단에서 이만큼 빠지면 방어에 들어가요.",
    "partial_exit_pct": "방어에 들어갈 때 들고 있는 것의 얼마를 팔지예요.",
    "short_take_profit_pct": "숏이 이만큼 이익 나면 덮어요.",
    "short_stop_loss_pct": "숏이 이만큼 손해 나면 덮어요.",
}

# **지금 들고 있는 포지션과 상관없는 칸.** 여기 있는 것만 "다음에 살 때 쓰는 값" 이고,
# 나머지는 전부 들고 있는 포지션의 청산에 걸릴 수 있다고 본다.
#
# 왜 반대로(청산 칸을 적는 식으로) 하지 않는가: 빠뜨렸을 때의 결과가 다르다. 청산 칸을
# 하나 빠뜨리면 사용자가 **묻지도 않은 채** 들고 있는 포지션의 조건이 바뀌고, 그 자리에서
# 팔릴 수도 있다. 진입 칸을 빠뜨리면 안 물어도 될 걸 한 번 더 묻는 것뿐이다.
#
# 추가 매수 계열(세이프티 주문 · DCA 주기)은 **들고 있는 동안 더 사므로** 여기 넣지 않는다.
ENTRY_ONLY_KEYS = ("initial_capital", "invest_ratio", "cooldown_minutes")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def form_view(macro: Macro, local_decisions: bool = False) -> list[dict]:
    """화면이 수정 칸을 그릴 재료 — **숫자로 고칠 수 있는 칸만**.

    왜 숫자만인가: 문자 칸(``ma_type`` SMA/EMA, ``strategy`` reversion/breakout)은 값이
    아니라 **구조의 선택**이다. 바꾸면 사실상 다른 매크로가 되는데, 조건 값 수정과 같은
    자리에 두면 사용자가 그 차이를 모르고 누른다. 그런 변경은 종료하고 새로 시작한다.

    왜 서버가 주는가: 무엇을 바꿀 수 있는지는 이 모듈이 정한다. 화면이 따로 목록을 들면
    두 벌이 되어 어긋난다 — 화면에는 보이는데 서버가 거절하는 칸이 생긴다.

    위험 설정은 값이 비어 있어도(손절 꺼 둠) 칸을 낸다 — 켜는 길이 있어야 한다.
    """
    out: list[dict] = []
    quote = str(getattr(macro, "quote_currency", "") or "")
    for section in EDITABLE_SECTIONS:
        values = _section(macro, section)
        for key, value in values.items():
            # params 는 들어 있는 숫자만. risk 는 꺼 둔 칸(None)도 낸다 — 켤 수 있어야 한다.
            if not _is_number(value) and not (section == "risk" and value is None):
                continue
            # 실행기가 직접 판단하면 조건 값은 실행기 엔진이 들고 있다 — 바꿔도 안 따라온다.
            if local_decisions and section == "params" and key not in LOCAL_EDITABLE_PARAMS:
                continue
            out.append({
                "path": f"{section}.{key}", "section": section, "key": key,
                "label": _VALUE_LABELS.get(key, key), "value": value,
                "unit": _UNITS.get(key, ""),
                # 입력 칸 옆에 붙는 단위(%, USDT, KRW, 시간, 봉 …).
                "unit_text": _unit_text(key, _UNITS.get(key, ""), quote),
                # 이 칸이 무슨 일을 하는가 — 화면이 칸 밑에 한 줄로 보여 준다.
                "help": _HELP.get(key, ""),
                # "next_entry" 면 지금 들고 있는 포지션과 상관없다. 그 밖은 "position" —
                # 바꾸면 들고 있는 포지션의 청산 조건이 바뀔 수 있어 적용 시점을 묻는다.
                "affects": "next_entry" if key in ENTRY_ONLY_KEYS else "position",
                # 끌 수 있는 칸인가 — 위험 설정만 비울 수 있다(손절 없음 등).
                "clearable": section == "risk" and key != "invest_ratio",
            })
    return out
