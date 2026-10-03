"""과최적화 경고는 서버의 결정론적 규칙이 낸다 — AI 가 아니다.

경고가 헛돌면 사용자가 무시하게 되고 기능이 죽는다. 떠야 할 때 뜨고
안 떠야 할 때 안 뜨는 것을 골든 케이스로 고정한다.
"""
from app.engine.backtest import EquityPoint
from app.engine import validation


def curve(points):
    return [EquityPoint(t=t, equity=e) for t, e in points]


OVERFIT = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 280.0),
    ("2026-02-28T00:00:00Z", 285.0), ("2026-03-31T00:00:00Z", 288.0),
    ("2026-04-30T00:00:00Z", 290.0),
])
HEALTHY = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 112.0),
    ("2026-02-28T00:00:00Z", 121.0), ("2026-03-31T00:00:00Z", 133.0),
    ("2026-04-30T00:00:00Z", 147.0),
])
# 한 달 안에서 끝나는 짧은 백테스트 — 그 한 달이 번 돈의 100% 를 차지하는 건 당연하다.
ONE_MONTH = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-15T00:00:00Z", 130.0),
    ("2026-01-31T00:00:00Z", 150.0),
])
# 두 달: 첫 달이 거의 전부를 번다(몫 = 80 / 90 = 88.89). 집중 판정의 하한선.
TWO_MONTHS = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 180.0),
    ("2026-02-28T00:00:00Z", 190.0),
])


def test_an_overfit_curve_raises_every_warning():
    found = validation.warnings(curve=OVERFIT, total_trades=5,
                                window_returns=[180.0, 2.0, -4.0, -1.0],
                                top_trade_share_pct=74.0)
    assert set(found) == set(validation.WARNING_CODES)


def test_a_healthy_curve_raises_nothing():
    assert validation.warnings(curve=HEALTHY, total_trades=120,
                               window_returns=[9.0, 11.0, 8.0, 12.0],
                               top_trade_share_pct=14.0) == []


def test_each_rule_fires_on_its_own():
    assert validation.warnings(curve=OVERFIT, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) \
        == ["한_구간_집중"]
    assert validation.warnings(curve=HEALTHY, total_trades=9,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) \
        == ["표본_부족"]
    assert validation.warnings(curve=HEALTHY, total_trades=120,
                               window_returns=[9.0, -1.0], top_trade_share_pct=14.0) \
        == ["후반부_음수"]
    assert validation.warnings(curve=HEALTHY, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=51.0) \
        == ["거래_집중"]


def test_no_trades_is_sample_shortage_not_a_crash():
    """거래가 없으면 곡선도 비어 있다 — 터지지 않고 표본 부족으로 센다."""
    assert validation.warnings(curve=[], total_trades=0, window_returns=[],
                               top_trade_share_pct=None) == ["표본_부족"]


def test_missing_optional_inputs_do_not_invent_warnings():
    assert validation.warnings(curve=HEALTHY, total_trades=120, window_returns=[],
                               top_trade_share_pct=None) == []


def test_a_single_month_window_cannot_show_concentration():
    """한 달짜리 구간은 분산을 볼 수 없다 — 몫이 100% 여도 집중 경고를 내지 않는다.

    이 경우에 맞는 경고는 표본 부족이고, 그건 거래 수로 따로 켜진다.
    """
    assert validation.concentration(ONE_MONTH)["top_month_share_pct"] == 100.0
    assert validation.concentration(ONE_MONTH)["months"] == 1
    assert validation.warnings(curve=ONE_MONTH, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) == []


def test_concentration_counts_from_the_second_month():
    """하한선 바로 위(두 달)에서는 집중 경고가 켜진다 — 상수가 너무 높아지지 않게 고정."""
    assert validation.concentration(TWO_MONTHS)["months"] == validation.MIN_MONTHS_FOR_CONCENTRATION
    assert validation.warnings(curve=TWO_MONTHS, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) \
        == ["한_구간_집중"]
