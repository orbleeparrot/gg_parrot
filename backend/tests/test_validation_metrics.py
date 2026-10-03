"""equity_curve 만 다시 읽어 내는 지표 — 백테스트를 다시 돌리지 않는다."""
from app.engine.backtest import EquityPoint
from app.engine import validation


def curve(points):
    return [EquityPoint(t=t, equity=e) for t, e in points]


def test_monthly_returns_splits_the_curve_by_calendar_month():
    rows = validation.monthly_returns(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 110.0),
        ("2026-02-28T00:00:00Z", 99.0),
    ]))
    assert [r["month"] for r in rows] == ["2026-01", "2026-02"]
    assert rows[0]["pct"] == 10.0
    assert rows[1]["pct"] == -10.0


def test_concentration_reports_the_biggest_month_share():
    result = validation.concentration(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 180.0),
        ("2026-02-28T00:00:00Z", 190.0), ("2026-03-31T00:00:00Z", 200.0),
    ]))
    assert result["months"] == 3
    assert result["top_month_share_pct"] == 80.0


def monthly(values):
    """월말 하루씩 한 점 — 달 하나에 한 점이라 달별 증가분을 손으로 셀 수 있다."""
    return curve([(f"2026-{i + 1:02d}-28T00:00:00Z", v) for i, v in enumerate(values)])


def test_concentration_is_high_when_one_month_made_almost_all_the_profit():
    result = validation.concentration(monthly([100.0, 280.0, 285.0, 288.0, 290.0]))
    assert result["top_month_share_pct"] == 94.74
    assert result["months"] == 5


def test_concentration_is_low_when_profit_is_spread_across_months():
    result = validation.concentration(monthly([100.0, 112.0, 121.0, 133.0, 147.0]))
    assert result["top_month_share_pct"] == 29.79


def test_concentration_counts_only_months_that_made_money():
    """100 → 150 → 100 → 130: 번 달은 +50 과 +30 뿐이라 50 / 80."""
    result = validation.concentration(monthly([100.0, 150.0, 100.0, 130.0]))
    assert result["top_month_share_pct"] == 62.5


def test_concentration_has_no_share_when_no_month_made_money():
    assert validation.concentration(monthly([100.0, 90.0, 80.0]))["top_month_share_pct"] is None


def test_drawdown_window_finds_depth_and_recovery():
    result = validation.drawdown_window(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-10T00:00:00Z", 60.0),
        ("2026-01-20T00:00:00Z", 100.0),
    ]))
    assert result["depth_pct"] == 40.0
    assert result["recovery_days"] == 10
    assert result["start"] == "2026-01-01T00:00:00+00:00"
    assert result["trough"] == "2026-01-10T00:00:00+00:00"
    assert result["recovered"] == "2026-01-20T00:00:00+00:00"


def test_a_curve_that_never_recovers_reports_no_recovery():
    result = validation.drawdown_window(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-10T00:00:00Z", 60.0),
    ]))
    assert result["recovered"] is None and result["recovery_days"] is None
    assert result["start"] == "2026-01-01T00:00:00+00:00"
    assert result["trough"] == "2026-01-10T00:00:00+00:00"


def test_too_short_a_curve_yields_no_metrics_instead_of_dividing_by_zero():
    """거래가 하나도 없으면 곡선이 비거나 두 점뿐이다 — 터지지 않아야 한다."""
    for short in ([], curve([("2026-01-01T00:00:00Z", 100.0)])):
        assert validation.monthly_returns(short) == []
        assert validation.concentration(short)["top_month_share_pct"] is None
        assert validation.drawdown_window(short) is None
        assert validation.sortino(short) is None
        assert validation.calmar(short, 0.0) is None


def test_a_two_point_flat_curve_is_a_normal_input_not_an_error():
    """신호가 하나도 없던 백테스트의 곡선 — 두 점이 같은 값이다."""
    flat = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-03-01T00:00:00Z", 100.0)])
    assert validation.monthly_returns(flat) == [{"month": "2026-01", "pct": 0.0},
                                                {"month": "2026-03", "pct": 0.0}]
    assert validation.concentration(flat)["top_month_share_pct"] is None
    assert validation.drawdown_window(flat) is None
    assert validation.sortino(flat) is None
    assert validation.calmar(flat, 0.0) is None
    assert validation.calmar(flat, 10.0) == 0.0


def test_sortino_is_pinned_to_a_hand_computed_value():
    """수익률 +10% · -10% · +10%: 평균 0.0333, 하방편차 0.1, 3 구간 → 0.0333 / 0.1 * sqrt(3)."""
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-01-02T00:00:00Z", 110.0),
                  ("2026-01-03T00:00:00Z", 99.0), ("2026-01-04T00:00:00Z", 108.9)])
    assert validation.sortino(rows) == 0.58


def test_sortino_is_none_when_nothing_ever_lost_money():
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-01-02T00:00:00Z", 110.0),
                  ("2026-01-03T00:00:00Z", 121.0)])
    assert validation.sortino(rows) is None


def test_calmar_is_pinned_to_a_hand_computed_value():
    """730 일(정확히 2년) 동안 100 → 121 이면 연 10%. 최대낙폭 5% 로 나누면 2.0."""
    rows = curve([("2025-01-01T00:00:00Z", 100.0), ("2027-01-01T00:00:00Z", 121.0)])
    assert validation.calmar(rows, 5.0) == 2.0


def test_calmar_is_none_when_the_curve_ends_at_or_below_zero():
    """equity 가 0 이하로 떨어질 수 있다 — 복소수가 되어 터지면 안 된다."""
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-12-31T00:00:00Z", -50.0)])
    assert validation.calmar(rows, 10.0) is None


def test_calmar_is_none_for_a_span_too_short_to_annualise():
    """한 시간 사이 +20% 를 1년으로 늘리면 1.2 의 8760 제곱 — 의미도 없고 넘친다."""
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-01-01T01:00:00Z", 120.0)])
    assert validation.calmar(rows, 10.0) is None


def test_calmar_is_none_when_the_drawdown_is_not_a_finite_number():
    """NaN 은 JSON 으로 내보낼 수 없다 — 응답 직렬화에서 500 이 된다."""
    rows = curve([("2025-01-01T00:00:00Z", 100.0), ("2027-01-01T00:00:00Z", 121.0)])
    assert validation.calmar(rows, float("nan")) is None
    assert validation.calmar(rows, float("inf")) is None


def test_calmar_is_none_when_there_was_no_drawdown():
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-12-31T00:00:00Z", 200.0)])
    assert validation.calmar(rows, 0.0) is None
