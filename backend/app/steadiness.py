"""꾸준함 — 요행으로 번 설정보다 꾸준히 번 설정을 카드 위로 올린다.

왜 필요한가(2026-10-29): ``ask.select_top`` 은 후보 90개를 **같은 과거 구간**에서 재고
그 구간 점수 1~3등을 카드로 낸다. 90개 중 그 구간에 가장 잘 맞은 것이 1등이 되므로,
이미 지나간 상승에 맞춰진 설정이 올라온다. 사용자 피드백("이미 어느정도 고점에 올라간
코인들을 추천목록으로 보여주는 것 같아")의 뒷면이 이것이다.

설계상 지키는 것:

* **거르지 않는다.** 순위에만 쓴다 — 문턱으로 쓰면 카드가 0장이 되는 길이고, 그건
  사용자가 물리친 설계다. 순서만 바꾸므로 카드 수는 구조적으로 줄어들 수 없다.
* **기존 필드만 쓴다.** ``BacktestResult`` 와 ``engine/`` 을 건드리지 않으므로 Python
  엔진과 Rust 실행기의 정합성 계약(engine_golden.json · ENGINE_VERSION)을 열지 않는다.
* **모른다고 벌주지 않는다.** 값을 못 구하면 깎지 않는다(1.0).
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

from engine.backtest import BacktestResult

# 배수의 바닥. 꾸준함이 순위를 **거르지는** 못하게 하는 장치다 — 아무리 요행이어도
# 수익이 큰 쪽이 뒤집히지 않을 만큼만 깎는다.
STEADY_FLOOR = 0.5
# 상위 세 거래 몫: 이 아래면 안 깎고(OK), 이 위면 다 깎는다(BAD). 사이는 비례.
CONCENTRATION_OK, CONCENTRATION_BAD, CONCENTRATION_WEIGHT = 50.0, 90.0, 0.3
# 후반 몫: 이 위면 안 깎고, 0 이면 다 깎는다. 0 은 '앞에서 다 벌고 뒤는 제자리'.
LATER_OK, LATER_BAD, LATER_WEIGHT = 40.0, 0.0, 0.3


def _moment(stamp: object) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None


def later_share(result: BacktestResult) -> Optional[float]:
    """구간 후반이 전체 수익에서 차지하는 몫(%). 앞에서 다 벌었으면 0 에 가깝다.

    **시간으로 자른다.** 지금 이 경로가 받는 곡선은 봉마다 한 점인 원본이다
    (``compact_backtest_result`` 는 응답을 만들 때만 쓴다 — main.py). 그래서 인덱스로
    잘라도 같은 답이 나온다. 그래도 시간으로 자르는 이유는 봉이 비는 구간(체결 없는
    봉은 거래소가 아예 빼고 준다)과, 나중에 누가 이 경로에 압축된 곡선을 흘려보낼
    경우에 조용히 틀리지 않기 위해서다.

    전체 수익이 0 이하면 '후반 몫' 이 뜻을 잃으므로 None 이다(``worth_the_macro`` 가 이미
    떨어뜨리는 결과다). 시각을 못 읽어도 None 이다.
    """
    curve = list(result.equity_curve or ())
    if len(curve) < 3:
        return None
    total = float(curve[-1].equity) - float(curve[0].equity)
    if total <= 0:
        return None

    started, ended = _moment(curve[0].t), _moment(curve[-1].t)
    if started is None or ended is None:
        return None
    try:
        middle = started + (ended - started) / 2
    except TypeError:          # tz 가 섞인 곡선 — 자를 자리를 못 정한다.
        return None

    split = 1
    for index in range(1, len(curve) - 1):
        moment = _moment(curve[index].t)
        if moment is None:
            return None
        if moment <= middle:
            split = index
    return round((float(curve[-1].equity) - float(curve[split].equity)) / total * 100, 2)


def _ramp(value: float, ok: float, bad: float) -> float:
    """``ok`` 에서 0, ``bad`` 에서 1 로 비례하는 깎음 비율. 두 방향 모두 쓴다."""
    span = bad - ok
    if span == 0:
        return 0.0
    return min(1.0, max(0.0, (value - ok) / span))


def steadiness(result: BacktestResult) -> float:
    """정렬 키에 곱할 꾸준함 배수. 범위는 [STEADY_FLOOR, 1.0].

    두 가지를 깎는다. 둘 다 **이미 계산돼 있으면서 아무도 안 쓰던** 값이다.

    * ``top_trade_share_pct`` 가 높다 = 상위 세 거래가 수익의 대부분이다(요행).
    * ``later_share`` 가 낮다 = 구간 앞쪽에서 다 벌었다(지나간 상승에 맞춰졌다).

    값을 못 구하면 그 항목은 깎지 않는다 — 모른다고 벌주지 않는다.
    """
    penalty = 0.0
    share = result.top_trade_share_pct
    if share is not None:
        try:
            number = float(share)
        except (TypeError, ValueError):
            number = math.nan
        if math.isfinite(number):
            penalty += CONCENTRATION_WEIGHT * _ramp(number, CONCENTRATION_OK, CONCENTRATION_BAD)
    later = later_share(result)
    if later is not None:
        penalty += LATER_WEIGHT * _ramp(later, LATER_OK, LATER_BAD)
    return round(max(STEADY_FLOOR, 1.0 - penalty), 4)
