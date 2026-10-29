"""꾸준함 — 요행으로 번 설정보다 꾸준히 번 설정을 카드 위로 올린다 (2026-10-29).

왜 필요한가: ``select_top`` 은 후보 90개를 같은 과거 구간에서 재고 그 구간 1~3등을
카드로 낸다. 90개 중 그 구간에 가장 잘 맞은 것이 1등이 되므로, 지나간 상승에 맞춰진
설정이 올라온다. 사용자가 아쉬워한 '이미 고점에 올라간 코인' 의 뒷면이다.

**문턱은 ``engine/validation.py`` 것을 그대로 쓴다.** 거기 숫자는 측정으로 맞춰져 있고
검증 화면이 경고를 띄우는 선이다. 따로 두면 같은 매크로를 두고 카드는 "꾸준히 벌었어",
검증 화면은 "거래_집중" 이라고 말하는 상태가 된다.

파생에 쓰는 값은 모두 기존 ``BacktestResult`` 필드다 — 결과 구조를 건드리지 않으므로
Python 엔진과 Rust 실행기의 정합성 계약을 열지 않는다.
"""
from __future__ import annotations

from app import steadiness as st
from engine.backtest import BacktestResult, EquityPoint
from engine.validation import (MIN_SPAN_DAYS_FOR_CONCENTRATION, TOP_MONTH_SHARE_LIMIT,
                               TOP_TRADE_SHARE_LIMIT)


def _curve(points: list[tuple[str, float]]) -> list[EquityPoint]:
    return [EquityPoint(t=f"{day}T12:00:00", equity=equity) for day, equity in points]


def _result(curve: list[EquityPoint], **kw) -> BacktestResult:
    base = dict(final_return_pct=10.0, win_rate_pct=50.0, mdd_pct=5.0, total_trades=20,
                initial_capital=100.0, final_equity=200.0, equity_curve=curve)
    base.update(kw)
    return BacktestResult(**base)


def _four_months(month_ends: list[float]) -> list[EquityPoint]:
    """넉 달에 걸친 곡선. 달마다 중간 점과 말일 점을 둔다.

    구간이 약 120일이라 MIN_SPAN_DAYS_FOR_CONCENTRATION(85일)을 넘고 달 수도 4라,
    validation.py 가 집중 판정을 켜는 조건을 모두 만족한다.
    """
    out: list[tuple[str, float]] = [("2026-01-01", 100.0)]
    for index, (middle, last) in enumerate(
            [("2026-01-15", "2026-01-31"), ("2026-02-15", "2026-02-28"),
             ("2026-03-15", "2026-03-31"), ("2026-04-15", "2026-04-30")]):
        out.append((middle, month_ends[index]))
        out.append((last, month_ends[index]))
    return _curve(out)


# 넉 달 내내 고르게 벌었다 — 한 달 몫 25%.
EVEN = _four_months([125.0, 150.0, 175.0, 200.0])
# 첫 달에 다 벌고 나머지는 제자리 — 한 달 몫 100%. '지나간 상승' 의 모양이다.
ONE_BURST = _four_months([200.0, 200.0, 200.0, 200.0])
# 같은 모양이지만 이틀짜리 — 짧은 구간에서는 몫에 정보가 없다.
SHORT_BURST = _curve([("2026-01-01", 100.0), ("2026-01-02", 200.0), ("2026-01-03", 200.0)])

SPREAD_TRADES = {"top_trade_share_pct": TOP_TRADE_SHARE_LIMIT - 10.0}
FLUKE_TRADES = {"top_trade_share_pct": 100.0}


def test_steadiness_is_full_when_gains_are_spread_over_months_and_trades():
    assert st.steadiness(_result(EVEN, **SPREAD_TRADES)) == 1.0


def test_steadiness_punishes_gains_packed_into_one_month():
    even = st.steadiness(_result(EVEN, **SPREAD_TRADES))
    burst = st.steadiness(_result(ONE_BURST, **SPREAD_TRADES))
    assert st.STEADY_FLOOR <= burst < even


def test_steadiness_punishes_gains_packed_into_a_few_trades():
    spread = st.steadiness(_result(EVEN, **SPREAD_TRADES))
    fluke = st.steadiness(_result(EVEN, **FLUKE_TRADES))
    assert st.STEADY_FLOOR <= fluke < spread


def test_steadiness_never_drops_below_the_floor():
    assert st.steadiness(_result(ONE_BURST, **FLUKE_TRADES)) == st.STEADY_FLOOR


def test_steadiness_leaves_a_short_window_alone():
    # validation.py 의 결론을 그대로 따른다: 구간이 짧으면 한 달 몫에 정보가 없다
    # ("달이 둘뿐이면 70/30 으로만 갈려도 몫이 70% 를 넘어"). 물어볼까의 기간에는
    # 1주와 1달이 있어서, 끄지 않으면 카드 3분의 1을 잡음으로 재정렬한다.
    assert st.steadiness(_result(SHORT_BURST, **SPREAD_TRADES)) == 1.0


def test_steadiness_starts_punishing_where_the_validation_screen_warns():
    # 검증 화면이 '거래_집중' 을 띄우지 않는 값에서는 순위도 깎지 않는다.
    just_under = {"top_trade_share_pct": TOP_TRADE_SHARE_LIMIT - 0.01}
    just_over = {"top_trade_share_pct": TOP_TRADE_SHARE_LIMIT + 20.0}
    assert st.steadiness(_result(EVEN, **just_under)) == 1.0
    assert st.steadiness(_result(EVEN, **just_over)) < 1.0


def test_steadiness_does_not_punish_unknown_trade_concentration():
    # 이긴 거래가 없으면 top_trade_share_pct 가 None 이다 — 모른다고 벌주지 않는다.
    assert st.steadiness(_result(EVEN, top_trade_share_pct=None)) == 1.0


def test_steadiness_does_not_punish_an_unreadable_curve():
    assert st.steadiness(_result([], **SPREAD_TRADES)) == 1.0


def test_the_thresholds_are_not_a_second_copy_of_validations_numbers():
    # 복사해 두면 한쪽만 고쳐져 카드와 검증 화면이 어긋난다.
    assert st.TRADE_SHARE_LIMIT is TOP_TRADE_SHARE_LIMIT
    assert st.MONTH_SHARE_LIMIT is TOP_MONTH_SHARE_LIMIT
    assert st.MIN_SPAN_DAYS is MIN_SPAN_DAYS_FOR_CONCENTRATION
