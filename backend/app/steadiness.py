"""꾸준함 — 요행으로 번 설정보다 꾸준히 번 설정을 카드 위로 올린다.

왜 필요한가(2026-10-29): ``ask.select_top`` 은 후보 90개를 **같은 과거 구간**에서 재고
그 구간 점수 1~3등을 카드로 낸다. 90개 중 그 구간에 가장 잘 맞은 것이 1등이 되므로,
이미 지나간 상승에 맞춰진 설정이 올라온다. 사용자 피드백("이미 어느정도 고점에 올라간
코인들을 추천목록으로 보여주는 것 같아")의 뒷면이 이것이다.

설계상 지키는 것:

* **거르지 않는다.** 순위에만 쓴다 — 문턱으로 쓰면 카드가 0장이 되는 길이고, 그건
  사용자가 물리친 설계다. 순서만 바꾸므로 카드 수는 구조적으로 줄어들 수 없다.
* **문턱을 새로 만들지 않는다.** ``engine/validation.py`` 의 것을 그대로 쓴다. 거기
  숫자는 측정으로 맞춰져 있고(그 파일 주석에 잰 값이 남아 있다) 검증 화면이 경고를
  띄우는 선이다. 따로 두면 같은 매크로를 두고 카드는 "꾸준히 벌었어", 검증 화면은
  "거래_집중" 이라고 말하는 상태가 된다. **깎기 시작하는 지점이 경고선과 같다.**
* **기존 필드만 쓴다.** ``BacktestResult`` 와 엔진의 판단 경로를 건드리지 않으므로
  Python 엔진과 Rust 실행기의 정합성 계약(engine_golden.json · ENGINE_VERSION)을 열지
  않는다(``engine.validation`` 은 LIVE_PATH 밖이다).
* **모른다고 벌주지 않는다.** 값을 못 구하면 그 항목은 깎지 않는다.
"""
from __future__ import annotations

import math

from engine.backtest import BacktestResult
from engine.validation import (MIN_MONTHS_FOR_CONCENTRATION, MIN_SPAN_DAYS_FOR_CONCENTRATION,
                               TOP_MONTH_SHARE_LIMIT, TOP_TRADE_SHARE_LIMIT, concentration,
                               span_days)

# validation.py 의 문턱을 **그대로** 가리킨다. 숫자를 베껴 쓰지 않는다 — 베끼면 한쪽만
# 고쳐져 카드와 검증 화면이 어긋난다(시험이 `is` 로 이 동일성을 고정한다).
TRADE_SHARE_LIMIT = TOP_TRADE_SHARE_LIMIT
MONTH_SHARE_LIMIT = TOP_MONTH_SHARE_LIMIT
MIN_SPAN_DAYS = MIN_SPAN_DAYS_FOR_CONCENTRATION
MIN_MONTHS = MIN_MONTHS_FOR_CONCENTRATION

# 배수의 바닥. 꾸준함이 순위를 **거르지는** 못하게 하는 장치다 — 아무리 요행이어도
# 수익이 크게 앞선 쪽이 뒤집히지 않을 만큼만 깎는다.
STEADY_FLOOR = 0.5
# 두 항목이 각각 최대 이만큼 깎는다. 둘이 겹치면 바닥에 닿는다.
TRADE_WEIGHT = 0.3
MONTH_WEIGHT = 0.3


def _ramp(value: float, warn_at: float, worst: float) -> float:
    """경고선(``warn_at``)에서 0, 최악(``worst``)에서 1 로 비례하는 깎음 비율.

    경고선 아래에서는 0 이다 — 검증 화면이 아무 말도 하지 않는 결과를 순위에서
    몰래 깎지 않는다.
    """
    span = worst - warn_at
    if span == 0:
        return 0.0
    return min(1.0, max(0.0, (value - warn_at) / span))


def _number(value: object) -> float:
    try:
        number = float(value)        # type: ignore[arg-type]
    except (TypeError, ValueError):
        return math.nan
    return number


def month_share(result: BacktestResult) -> float | None:
    """가장 많이 번 달이 전체 '번 돈' 에서 차지하는 몫(%). 판정할 수 없으면 None.

    ``validation.concentration`` 을 그대로 쓰고, **켜는 조건도 그대로 따른다**(달 수
    3 이상 · 구간 85일 이상). 그 하한이 있는 이유는 그 파일에 적혀 있다 — 구간이 짧으면
    몫이 달 수에 따라 거의 정해져 신호에 정보가 없다. 물어볼까의 기간에는 1주와 1달이
    있어서, 끄지 않으면 카드 상당수를 잡음으로 재정렬한다.
    """
    curve = result.equity_curve or []
    spread = concentration(curve)
    share = spread.get("top_month_share_pct")
    if share is None:
        return None
    if int(spread.get("months") or 0) < MIN_MONTHS:
        return None
    if span_days(curve) < MIN_SPAN_DAYS:
        return None
    number = _number(share)
    return number if math.isfinite(number) else None


def steadiness(result: BacktestResult) -> float:
    """정렬 키에 곱할 꾸준함 배수. 범위는 [STEADY_FLOOR, 1.0].

    두 가지를 깎는다. 둘 다 **이미 계산돼 있으면서 순위에는 안 쓰이던** 값이다.

    * ``top_trade_share_pct`` 가 높다 = 상위 몇 거래가 수익의 대부분이다(운에 가깝다).
    * 한 달 몫이 높다 = 번 돈이 한 구간에 몰렸다(지나간 상승에 맞춰졌다).
    """
    penalty = 0.0
    share = result.top_trade_share_pct
    if share is not None:
        number = _number(share)
        if math.isfinite(number):
            penalty += TRADE_WEIGHT * _ramp(number, TRADE_SHARE_LIMIT, 100.0)
    month = month_share(result)
    if month is not None:
        penalty += MONTH_WEIGHT * _ramp(month, MONTH_SHARE_LIMIT, 100.0)
    return round(max(STEADY_FLOOR, 1.0 - penalty), 4)
