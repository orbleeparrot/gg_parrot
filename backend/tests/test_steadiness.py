"""꾸준함 — 요행으로 번 설정보다 꾸준히 번 설정을 카드 위로 올린다 (2026-10-29).

왜 필요한가: ``select_top`` 은 후보 90개를 같은 과거 구간에서 재고 그 구간 1~3등을
카드로 낸다. 90개 중 그 구간에 가장 잘 맞은 것이 1등이 되므로, 지나간 상승에 맞춰진
설정이 올라온다. 사용자가 아쉬워한 '이미 고점에 올라간 코인' 의 뒷면이다.

여기 있는 함수는 모두 기존 ``BacktestResult`` 필드에서만 파생한다 — 엔진과 백테스트
결과 구조를 건드리지 않으므로 Python 엔진과 Rust 실행기의 정합성 계약을 열지 않는다.
"""
from __future__ import annotations

from app import steadiness as st
from engine.backtest import BacktestResult, EquityPoint


def _curve(pairs: list[tuple[int, float]]) -> list[EquityPoint]:
    """(시각 시(hour), 자본) 목록으로 자본 곡선을 만든다."""
    return [EquityPoint(t=f"2026-10-01T{hour:02d}:00:00", equity=equity) for hour, equity in pairs]


def _result(curve: list[EquityPoint], **kw) -> BacktestResult:
    base = dict(final_return_pct=10.0, win_rate_pct=50.0, mdd_pct=5.0, total_trades=10,
                initial_capital=100.0, final_equity=110.0, equity_curve=curve)
    base.update(kw)
    return BacktestResult(**base)


# --- later_share ---------------------------------------------------------------


def test_later_share_is_half_when_profit_is_even():
    curve = _curve([(0, 100.0), (1, 150.0), (2, 200.0)])
    assert st.later_share(_result(curve)) == 50.0


def test_later_share_is_zero_when_all_profit_came_early():
    # 고점 찍은 코인에서 나오는 모양 — 앞에서 다 벌고 뒤에서는 제자리.
    curve = _curve([(0, 100.0), (1, 200.0), (2, 200.0), (3, 200.0)])
    assert st.later_share(_result(curve)) == 0.0


def test_later_share_splits_by_time_not_by_index():
    # 봉이 비는 구간이 있으면 점 간격이 시간 간격과 어긋난다(체결 없는 봉은 거래소가
    # 아예 빼고 준다). 인덱스 중앙(t=2)으로 자르면 80.0, 시간 중앙(t=5 이전 마지막
    # 점 = t=3)이면 70.0 이다.
    curve = _curve([(0, 100.0), (1, 110.0), (2, 120.0), (3, 130.0), (10, 200.0)])
    assert st.later_share(_result(curve)) == 70.0


def test_later_share_is_unknown_without_profit():
    # 전체가 손실이면 '후반 몫' 이 뜻을 잃는다. select_top 이 이미 떨어뜨리는 결과다.
    curve = _curve([(0, 100.0), (1, 90.0), (2, 80.0)])
    assert st.later_share(_result(curve)) is None


def test_later_share_is_unknown_with_too_few_points():
    assert st.later_share(_result(_curve([(0, 100.0), (1, 110.0)]))) is None


# --- steadiness 배수 ------------------------------------------------------------
# 정렬 키에 곱하는 값이다. 범위는 [STEADY_FLOOR, 1.0] — 거르지 않고 순서만 바꾼다.
# 문턱 값 자체는 워크포워드 측정으로 확정하므로, 시험은 숫자가 아니라 관계를 고정한다.

_SPREAD = dict(top_trade_share_pct=40.0)      # 수익이 여러 거래에 퍼졌다
_FLUKE = dict(top_trade_share_pct=100.0)      # 상위 세 거래가 전부였다


def _even_curve():
    return _curve([(0, 100.0), (1, 150.0), (2, 200.0)])


def _front_loaded_curve():
    return _curve([(0, 100.0), (1, 200.0), (2, 200.0), (3, 200.0)])


def test_steadiness_is_full_when_profit_is_spread_and_even():
    assert st.steadiness(_result(_even_curve(), **_SPREAD)) == 1.0


def test_steadiness_never_drops_below_the_floor():
    worst = st.steadiness(_result(_front_loaded_curve(), **_FLUKE))
    assert worst == st.STEADY_FLOOR


def test_steadiness_punishes_concentration():
    spread = st.steadiness(_result(_even_curve(), **_SPREAD))
    fluke = st.steadiness(_result(_even_curve(), **_FLUKE))
    assert st.STEADY_FLOOR <= fluke < spread


def test_steadiness_punishes_profit_that_came_early():
    even = st.steadiness(_result(_even_curve(), **_SPREAD))
    early = st.steadiness(_result(_front_loaded_curve(), **_SPREAD))
    assert st.STEADY_FLOOR <= early < even


def test_steadiness_does_not_punish_unknown_concentration():
    # 이긴 거래가 없으면 top_trade_share_pct 가 None 이다 — 모른다고 벌주지 않는다.
    assert st.steadiness(_result(_even_curve(), top_trade_share_pct=None)) == 1.0


def test_steadiness_does_not_punish_an_unreadable_curve():
    # 곡선이 짧아 반으로 자를 수 없는 결과도 깎지 않는다.
    short = _curve([(0, 100.0), (1, 110.0)])
    assert st.steadiness(_result(short, **_SPREAD)) == 1.0
