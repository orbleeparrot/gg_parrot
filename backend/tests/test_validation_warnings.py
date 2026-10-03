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


def three_months(march_day, first_gain):
    """1/25 ~ 3/march_day 의 세 달 곡선. 총 증가 100 중 1월이 first_gain 을 번다."""
    rest = 100.0 - first_gain
    return curve([
        ("2026-01-25T00:00:00Z", 100.0),
        ("2026-01-31T00:00:00Z", 100.0 + first_gain),
        ("2026-02-28T00:00:00Z", 100.0 + first_gain + rest / 2),
        (f"2026-03-{march_day:02d}T00:00:00Z", 200.0),
    ])


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


def test_three_calendar_months_inside_the_span_floor_do_not_fire():
    """세 달에 걸쳐도 45일 미만이면 세지 않는다(1/25 ~ 3/10 = 44일)."""
    short = three_months(10, first_gain=80.0)
    assert validation.concentration(short)["months"] == 3
    assert validation.concentration(short)["top_month_share_pct"] >= 70.0
    assert validation.warnings(curve=short, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) == []


def test_span_floor_is_inclusive_at_45_days():
    """1/25 ~ 3/11 = 정확히 45일 — 이때부터 센다."""
    edge = three_months(11, first_gain=80.0)
    assert validation.warnings(curve=edge, total_trades=120,
                               window_returns=[9.0, 11.0], top_trade_share_pct=14.0) \
        == ["한_구간_집중"]


def test_month_share_limit_is_pinned_at_the_boundary():
    """몫 70.0 은 켜지고(이상), 71.0 도 켜지고, 69.0 은 안 켜진다."""
    def run(first_gain):
        return validation.warnings(curve=three_months(31, first_gain=first_gain),
                                   total_trades=120, window_returns=[9.0, 11.0],
                                   top_trade_share_pct=14.0)
    assert validation.concentration(three_months(31, 70.0))["top_month_share_pct"] == 70.0
    assert run(71.0) == ["한_구간_집중"]
    assert run(70.0) == ["한_구간_집중"]
    assert run(69.0) == []


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
