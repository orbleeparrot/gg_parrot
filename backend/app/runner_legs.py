"""묶음 매크로를 레그 하나짜리 단일 종목 매크로로 나눈다.

설계: docs/superpowers/specs/2026-10-30-portfolio-live-run-design.md

왜 서버가 나누는가: 실행기가 나눠 보내면 그 레그 매크로는 원본과 달라 ``macro_digest``
가 어긋나고, 모든 묶음이 "고쳐서 돌린 파일" 로 기록된다. 서명은 **원본 전체**로 확인해야
한다. 자금 분배도 한 곳에서 셈해야 레그 금액의 합이 묶음 금액과 어긋나지 않는다.

이 모듈은 DB 도 HTTP 도 모른다 — 매크로 하나와 번호를 받아 매크로 하나를 돌려준다.
"""
from __future__ import annotations

from typing import Optional

from engine.schema import Macro

# 묶음 한도가 걸린 묶음은 레그를 갈라 돌릴 수 없다 — "지금 몇 종목 들고 있나" 를 물을
# 자리가 없어진다. 거짓으로 돌리느니 거절하고 이유를 보여 준다.
BUNDLE_RISK_UNSUPPORTED = (
    "묶음 한도(한 번에 n종목 · 총 노출)를 건 묶음은 아직 실행기로 돌릴 수 없어요. "
    "한도를 빼고 다시 시작해 주세요.")


def leg_count(macro: Macro) -> int:
    """이 매크로가 몇 개의 실행으로 갈라지는가. 묶음이 아니면 1."""
    return len(macro.leg_specs()) or 1


def can_split(macro: Macro) -> bool:
    return macro.is_portfolio() and macro.bundle_risk is None


def leg_macro(macro: Macro, index: int) -> Macro:
    """묶음의 ``index`` 번째 레그를 단일 종목 매크로로.

    레그가 규칙을 덮어썼으면 그 규칙과 조건을 쓴다(``Macro.leg_rule`` · ``leg_filter`` 가
    정한 그대로다 — 여기서 다시 정하면 백테스트와 실거래가 갈린다).
    """
    specs = macro.leg_specs()
    if not specs:
        raise ValueError("묶음이 아닌 매크로는 나눌 수 없어요.")
    if index < 0 or index >= len(specs):
        raise ValueError("묶음에 없는 레그 번호예요.")
    if macro.bundle_risk is not None:
        raise ValueError(BUNDLE_RISK_UNSUPPORTED)

    leg = specs[index]
    body = macro.model_dump(mode="json")
    body["symbol"] = leg.symbol
    # 단일 종목이 된다 — 둘 다 비워야 Macro 가 묶음으로 읽지 않는다.
    body["symbols"] = None
    body["legs"] = None
    body["bundle_risk"] = None
    body["rule_type"] = macro.leg_rule(leg).value
    # 레그가 제 값을 들고 있으면 그것을 쓴다. 규칙을 바꾼 레그는 반드시 params 를 함께
    # 주므로(schema 의 PortfolioLeg 주석) 여기서 묶음 값과 섞지 않는다 — 섞으면 다른 규칙의
    # 칸이 조용히 끼어든다.
    base_params = dict(leg.params) if leg.params is not None else dict(macro.params or {})
    if base_params.get("initial_capital") is not None:
        base_params["initial_capital"] = leg_capital(macro, index)
    body["params"] = base_params
    entry_filter = macro.leg_filter(leg)
    body["entry_filter"] = entry_filter.model_dump(mode="json") if entry_filter is not None else None
    # 슬러그·공유는 묶음의 것이다 — 레그가 그 이름을 쓰면 묶음 카드와 뒤섞인다.
    body["macro_id"] = None
    body["share_slug"] = None
    return Macro(**body)


def leg_capital(macro: Macro, index: int) -> float:
    """레그에 들어갈 금액 — 묶음 금액 × 비중.

    **합이 묶음 금액과 어긋나지 않게** 마지막 레그가 나머지를 받는다. 비중이 33.3333 처럼
    나누어떨어지지 않을 때 레그마다 따로 반올림하면 합이 몇 원씩 모자라거나 넘친다.
    """
    specs = macro.leg_specs()
    total = float((macro.params or {}).get("initial_capital") or 0.0)
    if total <= 0:
        return 0.0
    shares = [round(total * float(leg.weight) / 100.0, 8) for leg in specs]
    shares[-1] = round(total - sum(shares[:-1]), 8)
    return shares[index]


def split(macro: Macro) -> list[Macro]:
    """묶음을 레그 매크로 전부로. 묶음이 아니면 자기 자신 하나."""
    if not macro.is_portfolio():
        return [macro]
    return [leg_macro(macro, index) for index in range(leg_count(macro))]


def bundle_view(macro: Macro) -> Optional[dict]:
    """화면·실행기가 "몇 개로 갈라지는지" 를 읽는 모양. 묶음이 아니면 ``None``."""
    if not macro.is_portfolio():
        return None
    specs = macro.leg_specs()
    return {
        "size": len(specs),
        "splittable": macro.bundle_risk is None,
        "reason": "" if macro.bundle_risk is None else BUNDLE_RISK_UNSUPPORTED,
        "legs": [{"index": index, "symbol": leg.symbol, "weight": float(leg.weight),
                  "capital": leg_capital(macro, index)}
                 for index, leg in enumerate(specs)],
    }
