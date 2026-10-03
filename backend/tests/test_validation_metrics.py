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


def test_drawdown_window_finds_depth_and_recovery():
    result = validation.drawdown_window(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-10T00:00:00Z", 60.0),
        ("2026-01-20T00:00:00Z", 100.0),
    ]))
    assert result["depth_pct"] == 40.0
    assert result["recovery_days"] == 10


def test_a_curve_that_never_recovers_reports_no_recovery():
    result = validation.drawdown_window(curve([
        ("2026-01-01T00:00:00Z", 100.0), ("2026-01-10T00:00:00Z", 60.0),
    ]))
    assert result["recovered"] is None and result["recovery_days"] is None


def test_too_short_a_curve_yields_no_metrics_instead_of_dividing_by_zero():
    """거래가 하나도 없으면 곡선이 비거나 두 점뿐이다 — 터지지 않아야 한다."""
    for short in ([], curve([("2026-01-01T00:00:00Z", 100.0)])):
        assert validation.monthly_returns(short) == []
        assert validation.concentration(short)["top_month_share_pct"] is None
        assert validation.drawdown_window(short) is None
        assert validation.sortino(short) is None
        assert validation.calmar(short, 0.0) is None


def test_calmar_is_none_when_there_was_no_drawdown():
    rows = curve([("2026-01-01T00:00:00Z", 100.0), ("2026-12-31T00:00:00Z", 200.0)])
    assert validation.calmar(rows, 0.0) is None
