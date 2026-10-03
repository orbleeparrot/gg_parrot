"""과최적화 경고는 서버의 결정론적 규칙이 낸다 — AI 가 아니다.

경고가 헛돌면 사용자가 무시하게 되고 기능이 죽는다. 떠야 할 때 뜨고
안 떠야 할 때 안 뜨는 것을 골든 케이스로 고정한다.
"""
from datetime import datetime, timedelta

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
# 두 달: 첫 달이 거의 전부를 번다(몫 88.89). 달이 둘뿐이면 몫이 거의 정해져 버린다.
TWO_MONTHS = curve([
    ("2026-01-01T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 180.0),
    ("2026-02-28T00:00:00Z", 190.0),
])
# 나흘짜리인데 월말을 낀다 — 걸친 달은 2 개, 몫은 75.0. 달 수만 보면 헛경고가 난다.
FOUR_DAY_STRADDLE = curve([
    ("2026-01-30T00:00:00Z", 100.0), ("2026-01-31T00:00:00Z", 101.0),
    ("2026-02-01T00:00:00Z", 103.0), ("2026-02-02T00:00:00Z", 104.0),
])


def dense(start, days, gain_on):
    """하루 한 점씩 찍은 촘촘한 곡선 — 엔진이 캔들마다 한 점을 내는 모양이다.

    start 날짜에서 100 으로 출발해, 하루 지날 때마다 gain_on(그날 날짜) 만큼 늘어난다.
    """
    equity, points = 100.0, []
    for offset in range(days + 1):
        day = start + timedelta(days=offset)
        if offset:
            equity += gain_on(day)
        points.append((day.strftime("%Y-%m-%dT00:00:00Z"), equity))
    return curve(points)


def by_month(start, days, totals):
    """달별 총 증가분(totals: {월: 금액})을 그 달의 날들에 고르게 나눈 촘촘한 곡선."""
    steps = {}
    for offset in range(1, days + 1):
        month = (start + timedelta(days=offset)).month
        steps[month] = steps.get(month, 0) + 1
    return dense(start, days, lambda day: totals.get(day.month, 0.0) / steps[day.month])


def warn(c):
    """거래는 충분하고 나머지 입력은 건강한 상태에서 곡선만 바꿔 본다."""
    return validation.warnings(curve=c, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0)


def test_an_overfit_curve_raises_every_warning():
    found = validation.warnings(curve=OVERFIT, total_trades=5,
                                window_returns=[180.0, 2.0, -4.0, -1.0],
                                top_trade_share_pct=74.0)
    assert found == list(validation.WARNING_CODES)   # 집합이 아니라 순서까지


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


def test_two_calendar_months_are_not_enough_for_concentration():
    """달이 둘이면 어떤 70/30 분할도 몫 70% 를 넘는다 — 정보가 없어 경고하지 않는다."""
    assert validation.concentration(TWO_MONTHS)["months"] == 2
    assert validation.concentration(TWO_MONTHS)["top_month_share_pct"] >= 70.0
    assert validation.warnings(curve=TWO_MONTHS, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) == []


def test_a_four_day_backtest_across_a_month_end_is_not_concentration():
    """걸친 달 수는 경과 시간이 아니다 — 월말을 낀 나흘짜리가 경고를 내면 안 된다."""
    assert validation.concentration(FOUR_DAY_STRADDLE)["months"] == 2
    assert validation.concentration(FOUR_DAY_STRADDLE)["top_month_share_pct"] == 75.0
    assert validation.warnings(curve=FOUR_DAY_STRADDLE, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) == []


def test_a_dense_growth_slowdown_is_not_concentration():
    """성장이 둔해진 것뿐인 60일 곡선 — 과최적화가 아니다. 이 라운드의 회귀 시험.

    1/31 에서 출발해 28일은 하루 +1, 이후는 하루 +0.35. 가장자리 달(1월 · 4월)은
    번 돈이 거의 없어 실질 구간이 둘뿐이고, 몫이 70% 를 넘는다. 45일 하한이면 켜졌다.
    """
    start = datetime(2026, 1, 31)
    slowdown = dense(start, 60, lambda day: 1.0 if (day - start).days <= 28 else 0.35)
    spread = validation.concentration(slowdown)
    # 선행 조건: 45일 하한으로는 막지 못하는 모양이어야 이 시험이 의미가 있다.
    assert spread["months"] >= validation.MIN_MONTHS_FOR_CONCENTRATION
    assert spread["top_month_share_pct"] >= validation.TOP_MONTH_SHARE_LIMIT
    assert validation._span_days(slowdown) >= 45.0
    assert warn(slowdown) == []


def test_a_dense_curve_with_one_month_holding_the_gain_still_fires():
    """기간 하한이 있어도 규칙이 죽지 않는다 — 120일 중 2월 한 달만 번 곡선."""
    spike = by_month(datetime(2026, 1, 1), 120, {2: 100.0})
    assert validation.concentration(spike)["top_month_share_pct"] == 100.0
    assert warn(spike) == ["한_구간_집중"]


def test_a_dense_constant_growth_curve_is_not_concentration():
    """매일 같은 만큼 느는 120일 곡선은 달마다 번 돈이 고르다."""
    steady = dense(datetime(2026, 1, 1), 120, lambda day: 1.0)
    assert validation.concentration(steady)["top_month_share_pct"] < 40.0
    assert warn(steady) == []


def test_span_floor_is_inclusive_at_85_days():
    """1/1 ~ 3/27 = 정확히 85일부터 센다. 하루 모자란 3/26 까지는 안 센다."""
    totals = {2: 100.0}
    just_under = by_month(datetime(2026, 1, 1), 84, totals)
    exactly = by_month(datetime(2026, 1, 1), 85, totals)
    assert validation.concentration(just_under)["months"] >= 3
    assert validation._span_days(just_under) == 84.0
    assert validation._span_days(exactly) == 85.0
    assert warn(just_under) == []
    assert warn(exactly) == ["한_구간_집중"]


def test_the_default_3m_daily_preset_span_is_covered():
    """기본 기간(3m, 일봉)은 캔들 경계 정규화 뒤 정확히 89.0일이다 — 규칙이 꺼지면 안 된다.

    하한을 90 으로 두면 기본 모양에서 한 달 몰빵 곡선도 못 잡는다. 이 시험이 그걸 막는다.
    """
    for start in (datetime(2026, 1, 1), datetime(2026, 1, 20)):
        spike = by_month(start, 89, {(start + timedelta(days=40)).month: 100.0})
        assert validation._span_days(spike) == 89.0
        assert validation.concentration(spike)["top_month_share_pct"] == 100.0
        assert warn(spike) == ["한_구간_집중"]


def test_a_dense_slowdown_in_the_85_to_89_day_band_is_not_concentration():
    """85~89일 구간에서도 성장이 2.9 배 둔해진 것뿐인 곡선은 경고하지 않는다.

    45일 하한을 깬 모양(하루 +1 을 28일, 이후 +0.35)을 시작일을 바꿔가며 고정한다.
    시작일: 연초, 월 중순, 연말(해 넘김), 1/31.
    """
    for start in (datetime(2026, 1, 1), datetime(2026, 1, 15),
                  datetime(2025, 12, 20), datetime(2026, 1, 31)):
        for days in range(85, 90):
            slowdown = dense(start, days, lambda day, s=start: 1.0 if (day - s).days <= 28 else 0.35)
            assert warn(slowdown) == [], (start, days)


def test_month_share_limit_is_pinned_at_the_boundary():
    """몫 70.0 은 켜지고(이상), 71.0 도 켜지고, 69.0 은 안 켜진다."""
    def spread(first_gain):
        rest = (100.0 - first_gain) / 2
        return by_month(datetime(2026, 1, 1), 120, {1: first_gain, 2: rest, 3: rest})
    assert validation.concentration(spread(70.0))["top_month_share_pct"] == 70.0
    assert warn(spread(71.0)) == ["한_구간_집중"]
    assert warn(spread(70.0)) == ["한_구간_집중"]
    assert warn(spread(69.0)) == []


def test_a_curve_with_mixed_timestamp_styles_is_not_measured_not_a_crash():
    """시각 표기가 섞여(Z 있음 · 없음) 기간을 못 재면 집중 규칙은 켜지 않는다."""
    mixed = curve([
        ("2026-01-01T00:00:00", 100.0), ("2026-01-31T00:00:00Z", 280.0),
        ("2026-02-28T00:00:00Z", 285.0), ("2026-04-30T00:00:00Z", 290.0),
    ])
    assert warn(mixed) == []


def test_trade_count_limit_is_pinned_at_the_boundary():
    """거래 10건은 통과, 9건은 표본 부족."""
    assert validation.warnings(curve=HEALTHY, total_trades=10, window_returns=[],
                               top_trade_share_pct=None) == []
    assert validation.warnings(curve=HEALTHY, total_trades=9, window_returns=[],
                               top_trade_share_pct=None) == ["표본_부족"]


def test_top_trade_share_limit_is_pinned_at_the_boundary():
    """상위 거래 몫 50.0 은 경고(이상), 49.9 는 통과."""
    assert validation.warnings(curve=HEALTHY, total_trades=120, window_returns=[],
                               top_trade_share_pct=50.0) == ["거래_집중"]
    assert validation.warnings(curve=HEALTHY, total_trades=120, window_returns=[],
                               top_trade_share_pct=49.9) == []


def test_codes_come_out_in_warning_codes_order_on_partial_overlap():
    """일부만 켜져도 WARNING_CODES 의 순서를 따른다."""
    assert validation.warnings(curve=HEALTHY, total_trades=9, window_returns=[9.0, 11.0],
                               top_trade_share_pct=51.0) == ["표본_부족", "거래_집중"]
    assert validation.warnings(curve=OVERFIT, total_trades=120, window_returns=[9.0, -1.0],
                               top_trade_share_pct=51.0) \
        == ["한_구간_집중", "후반부_음수", "거래_집중"]


def test_only_the_last_window_counts_and_one_window_is_not_enough():
    """후반부 = 마지막 구간. 앞 구간이 음수여도, 구간이 하나뿐이어도 경고하지 않는다."""
    kwargs = dict(curve=HEALTHY, total_trades=120, top_trade_share_pct=14.0)
    assert validation.warnings(window_returns=[-5.0, 3.0], **kwargs) == []
    assert validation.warnings(window_returns=[-1.0], **kwargs) == []
    assert validation.warnings(window_returns=[3.0, -0.01], **kwargs) == ["후반부_음수"]


def test_an_unmeasured_last_window_is_not_a_warning_and_not_a_crash():
    """구간에 캔들이 없으면 return_pct 가 None 으로 온다 — 못 잰 것이지 나쁜 게 아니다."""
    kwargs = dict(curve=HEALTHY, total_trades=120, top_trade_share_pct=14.0)
    assert validation.warnings(window_returns=[3.0, None], **kwargs) == []
    assert validation.warnings(window_returns=[3.0, float("nan")], **kwargs) == []
    assert validation.warnings(window_returns=[3.0, "n/a"], **kwargs) == []
    # 마지막이 아닌 자리의 None 은 영향이 없다 — 판정은 마지막 구간만 본다.
    assert validation.warnings(window_returns=[None, -3.0], **kwargs) == ["후반부_음수"]
    assert validation.warnings(window_returns=[None, 3.0], **kwargs) == []
