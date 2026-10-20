"""엔진 버전 — 실행기가 스스로 판단해도 백테스트와 같은 답을 내는지 가르는 숫자.

진입 판단이 실행기로 내려가면 엔진이 사용자 PC 마다 **다른 버전**으로 돌아간다.
웹에서 본 백테스트는 서버의 최신 엔진이 낸 결과인데, 손에 든 실행기가 옛 엔진이면
같은 매크로가 다른 자리에서 사고판다. 사용자는 그 차이를 볼 방법이 없다.

그래서 매크로마다 "이 규칙을 바르게 판단하려면 엔진이 최소 몇이어야 하는가" 를 정하고,
그보다 낮은 실행기에는 세션을 열어 주지 않는다. 늦게 사는 것보다 **다르게 사는 것**이
나쁘다 — 늦은 건 보이지만 다른 건 안 보인다.

## 언제 올리나

``ENGINE_VERSION`` 은 **판정이 달라질 때만** 올린다:

* 진입 · 청산 조건의 계산이 바뀐다(기준선 공식, 봉의 어느 값을 보는가, 판정 시점)
* 규칙 유형이나 파라미터가 새로 생긴다
* 체결 · 수수료 · 수량 계산이 바뀐다

올리지 **않는** 것: 화면 문구, 로그, 성능, 서버 전용 코드(백테스트 · 리포트),
결과가 같은 리팩터링.

## 올리는 법

1. ``ENGINE_VERSION`` 을 1 올린다.
2. 그 변경이 **어떤 매크로의 판정을 바꾸는지** 보고 ``_MIN_ENGINE_BY_RULE`` 에 적는다.
   규칙 유형 전체가 아니라 특정 파라미터만 영향을 받으면 ``required_engine_version``
   안에서 그 조건을 본다.
3. 적지 않으면 게이트는 통과시킨다 — 옛 실행기가 조용히 다른 답을 낸다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:  # 런타임 import 를 피한다 — schema 가 이 모듈을 쓰게 될 수도 있다
    from .schema import Macro

#: 이 저장소의 판단 코드 세대. 실행기는 빌드될 때 이 값을 함께 묶어 가고,
#: 세션을 시작할 때 서버에 알린다.
ENGINE_VERSION = 1

#: 규칙 유형 → 그 유형을 **바르게** 판단하는 데 필요한 최소 엔진 버전.
#: 비어 있으면 전부 1 — 아직 판정이 갈린 적이 없다.
_MIN_ENGINE_BY_RULE: dict[str, int] = {}


def required_engine_version(macro: Optional["Macro"]) -> int:
    """``macro`` 를 실행기가 스스로 판단하려면 필요한 최소 엔진 버전.

    매크로를 모르면(옛 실행기가 안 보냈거나 깨졌다) 1 — 알 수 없는 것을 이유로
    막지는 않는다. 그 경우는 이미 다른 게이트(매크로 필수 · 거래소 · 지표형)가 본다.
    """
    if macro is None:
        return 1
    rule = getattr(getattr(macro, "rule_type", None), "value", None) or str(
        getattr(macro, "rule_type", "") or ""
    )
    return max(1, _MIN_ENGINE_BY_RULE.get(rule, 1))


def parse_engine_version(value) -> int:
    """실행기가 말한 엔진 버전을 정수로. 못 알아들으면 0(= 모름).

    0 은 "엔진을 안 싣고 다니는 실행기" 다 — 서버가 판단해 주던 옛 모델. 그 실행기에게는
    이 게이트가 할 말이 없으므로 통과시킨다(``meets_engine_version`` 참고).
    """
    text = str(value if value is not None else "").strip()
    if not text.isascii() or not text.isdigit() or len(text) > 6:
        return 0
    return int(text)


def meets_engine_version(reported, macro: Optional["Macro"]) -> bool:
    """실행기가 이 매크로를 판단해도 되는가.

    버전을 안 밝힌 실행기(0)는 **막지 않는다.** 지금은 판단을 서버가 하므로 그 실행기의
    엔진이 몇이든 결과에 영향이 없다. 실행기가 스스로 판단하게 되면 그쪽에서 버전을
    싣고 오고, 그때부터 이 비교가 실제로 일을 한다.
    """
    version = parse_engine_version(reported)
    if version <= 0:
        return True
    return version >= required_engine_version(macro)
