"""성향 재설계(2026-10-12) — 상한 · 거래 수 · 봉 · 세션 이관 · 빈 화면 숫자.

왜 따로 두나: test_ask.py 는 이미 1,200줄이 넘고 끝점 흐름을 다룬다. 이 파일은 성향을
가르는 **순수 함수**들만 본다 — 값이 바뀌면 여기만 보면 된다.
"""
from __future__ import annotations

import pandas as pd

from app import ask
from app.engine.backtest import BacktestResult, _buy_hold_mdd_pct, run_backtest
from app.engine.schema import Macro


def _df(closes: list[float]) -> pd.DataFrame:
    stamps = pd.date_range("2026-01-01", periods=len(closes), freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": stamps, "open": closes, "high": closes,
        "low": closes, "close": closes, "volume": [1.0] * len(closes),
    })


def _macro(**over) -> Macro:
    body = {
        "symbol": "KRW-BTC", "exchange": "upbit", "quote_currency": "KRW",
        "rule_type": "A", "position_side": "long", "market": "spot",
        "leverage": 1, "candle_interval": "1h",
        "params": {"take_profit_pct": 3, "initial_capital": 1_000_000},
        "risk": {"stop_loss_pct": 2}, "period": {"preset": "1w"},
        "fees": {"commission_pct": 0.05, "slippage_pct": 0.05},
    }
    body.update(over)
    return Macro.model_validate(body)


def test_hold_mdd_is_the_peak_to_trough_of_the_price_itself():
    # 100 → 120 → 60 → 90. 고점 120 에서 저점 60 이니 -50%.
    assert _buy_hold_mdd_pct([100.0, 120.0, 60.0, 90.0]) == 50.0
    # 쭉 올랐으면 낙폭이 없다.
    assert _buy_hold_mdd_pct([100.0, 110.0, 130.0]) == 0.0
    # 봉이 하나면 '보유' 를 정의할 수 없다 — 0 이라고 말하면 거짓이다.
    assert _buy_hold_mdd_pct([100.0]) is None
    assert _buy_hold_mdd_pct([]) is None


def test_backtest_reports_hold_mdd_beside_hold_return():
    result = run_backtest(_macro(), _df([100.0, 120.0, 60.0, 90.0]))
    assert result.buy_hold_mdd_pct == 50.0
    assert result.buy_hold_return_pct is not None, "홀딩 수익률과 같이 나와야 한다"


def _evaluated(rule_type: str, *, trades: int, mdd: float, ret: float,
               hold: float | None, hold_mdd: float | None = None) -> ask.Evaluated:
    """백테스트를 돌리지 않고 선별 관문만 보기 위한 가짜 결과.

    rule_type 은 "A" 와 "J" 만 받는다 — MAX_PER_TYPE 가 1 이라 같은 유형이 두 칸을
    차지하지 못하는 것을 보려면 서로 다른 유형 둘이면 충분하다.
    """
    assert rule_type in ("A", "J"), "A 와 J 만 쓴다"
    result = BacktestResult(
        final_return_pct=ret, win_rate_pct=50.0, mdd_pct=mdd, total_trades=trades,
        initial_capital=1_000_000.0, final_equity=1_000_000.0 * (1 + ret / 100.0),
        equity_curve=[], buy_hold_return_pct=hold, buy_hold_mdd_pct=hold_mdd,
    )
    macro = _macro() if rule_type == "A" else _macro(
        rule_type="J", params={"ma_type": "SMA", "fast_period": 20, "slow_period": 60,
                               "initial_capital": 1_000_000}, risk={})
    return ask.Evaluated(ask.Candidate(f"{rule_type} 시험", macro, "template"), result)


def test_mdd_cap_grows_with_the_period():
    # 코인이 6개월간 20% 안 빠지는 일은 거의 없다. 1주와 6개월에 같은 숫자를 요구하면
    # 긴 기간이 전멸한다 — 측정: 균형형 6개월에서 상한 사망 72/87.
    assert ask.mdd_cap("stable", "1w") == 8.0
    assert ask.mdd_cap("stable", "6m") == 25.0
    assert ask.mdd_cap("balanced", "1w") == 15.0
    assert ask.mdd_cap("balanced", "6m") == 40.0
    assert ask.mdd_cap("aggressive", "6m") is None, "공격형은 상한이 없다"


def test_mdd_cap_follows_the_market_when_holding_barely_moved():
    # 홀딩이 10% 밖에 안 흔들린 구간에서 안정형이 18% 까지 허용하면 '안정' 이 아니다.
    assert ask.mdd_cap("stable", "3m", 10.0) == 6.0          # min(18, max(5, 10×0.6))
    assert ask.mdd_cap("balanced", "3m", 10.0) == 10.0       # min(32, max(5, 10×1.0))
    # 홀딩이 60% 빠진 구간이라도 절대 상한을 넘기지는 않는다.
    assert ask.mdd_cap("stable", "3m", 60.0) == 18.0
    # 홀딩이 거의 안 흔들린 구간에서 상한이 0 으로 수렴해 전부 탈락하는 것을 막는다.
    assert ask.mdd_cap("stable", "3m", 1.0) == 5.0
    assert ask.mdd_cap("aggressive", "3m", 1.0) is None


def test_mdd_cap_falls_back_to_the_absolute_table_without_a_hold_reading():
    # 묶음 집계와 봉 2개 미만은 홀딩 MDD 를 못 구한다. 그래도 걸러야 하고, 터지면 안 된다.
    assert ask.mdd_cap("stable", "3m", None) == 18.0
    assert ask.mdd_cap("stable", "3m", float("nan")) == 18.0
    assert ask.mdd_cap("stable", "3m", "몰라") == 18.0
    # 표에 없는 기간은 가운데(3개월) 값으로 둔다 — 조용히 상한을 풀지 않는다.
    assert ask.mdd_cap("balanced", "2d") == 32.0


def test_min_trades_follows_the_bar_count():
    # 4시간봉 1주 = 42봉인데 3회를 요구하던 것이 안정형 '며칠' 전멸의 원인이었다(50/57).
    assert ask.expected_bars("1w", "4h") == 42
    assert ask.min_trades_for(42) == 2
    assert ask.expected_bars("3m", "4h") == 546
    assert ask.min_trades_for(546) == 3
    assert ask.expected_bars("6m", "4h") == 1092
    assert ask.min_trades_for(1092) == 7
    assert ask.min_trades_for(0) == 2, "봉이 없어도 바닥은 2 회다"


def test_select_top_counts_trades_against_the_window_length():
    short_window = [_evaluated("J", trades=2, mdd=5.0, ret=20.0, hold=1.0)]
    assert [e.candidate.macro.rule_type.value
            for e in ask.select_top(short_window, "balanced", period="1w", interval="4h")] == ["J"]
    # 같은 2회가 6개월 창에서는 모자라다(7회 필요).
    assert ask.select_top(short_window, "balanced", period="6m", interval="4h") == []


def test_balanced_score_orders_differently_from_aggressive():
    # 측정에서 균형형(수익 − 0.5×MDD)과 공격형(수익)이 상위 3장을 똑같이 냈다 —
    # 초과 수익이 +25~+128%p 로 벌어진 자리에서 10~20 을 빼도 순서가 안 바뀌기 때문이다.
    # 이 두 값이 바로 그 자리다: 뺄셈은 wild(75-12.5=62.5)를 calm(60-4.5=55.5) 위에 두고,
    # 나눗셈은 calm(60/3=20)을 wild(75/5=15) 위에 둔다. 낙폭이 비율로 작용해야 순서가 바뀐다.
    calm = _evaluated("J", trades=9, mdd=9.0, ret=60.0, hold=0.0).result
    wild = _evaluated("A", trades=9, mdd=25.0, ret=75.0, hold=0.0).result
    assert ask.score("aggressive", wild) > ask.score("aggressive", calm), "공격형은 수익만 본다"
    assert ask.score("balanced", calm) > ask.score("balanced", wild), "균형형은 낙폭을 나눈다"
    assert ask.score("stable", calm) > ask.score("stable", wild)


def test_three_profiles_and_four_watch_levels():
    assert tuple(ask.PROFILES) == ("stable", "balanced", "aggressive")
    assert ask.WATCH_LEVELS == ("rarely", "sometimes", "often", "constantly")
    # 성향 표에 남는 칸은 화면 이름과 선물 허용 여부뿐이다 — 종목·방식·상한은 다른 데서 온다.
    for rules in ask.PROFILES.values():
        assert set(rules) == {"label", "futures"}


def test_watch_frequency_alone_decides_the_candle():
    assert ask.to_interval("rarely", "6m") == "1d"
    assert ask.to_interval("sometimes", "6m") == "4h"
    assert ask.to_interval("often", "6m") == "1h"
    assert ask.to_interval("constantly", "6m") == "15m"
    # 15분봉 × 1년 = 35,040봉으로 봉 상한(20,000)을 넘는 유일한 조합이다. 거절이 아니라
    # 한 칸 넓힌다 — 사용자는 '아주 자주 봐요' 를 고른 것이지 1년을 포기한 게 아니다.
    assert ask.to_interval("constantly", "1y") == "1h"
    # 모르는 값은 가운데로 — 조용히 가장 짧은 봉을 주면 안 된다.
    assert ask.to_interval("뭐라고요", "6m") == "4h"


def test_horizon_alone_decides_the_period():
    assert ask.to_period("days") == "1w"
    assert ask.to_period("weeks") == "3m"
    assert ask.to_period("months") == "6m"
    assert ask.to_period("long") == "1y"


def test_every_profile_sees_all_eight_rule_types():
    # 안정형이 8가지 중 4가지만 보던 탓에 후보가 57개(공격형 114개)였다.
    for profile in ask.PROFILES:
        plan = ask._Plan(risk_profile=profile, market="spot", leverage=1, symbols=["KRW-BTC"],
                         period_preset="3m", interval="4h", exchange="upbit")
        assert set(ask._allowed_types(plan)) == set(ask.ALL_RULE_TYPES)
    # C(적립식)는 레버리지·선물을 못 쓴다 — 이 제약만 남는다.
    futures = ask._Plan(risk_profile="aggressive", market="futures", leverage=2,
                        symbols=["BTCUSDT"], period_preset="3m", interval="4h")
    assert "C" not in ask._allowed_types(futures)


def test_short_bar_presets_layer_over_the_normal_table():
    # 짧은 봉 표는 A·E·F·G·J 다섯뿐이다. 그것만 쓰면 15분봉을 고른 순간 방식이 다섯으로
    # 줄어 화이트리스트를 없앤 효과가 사라지고, build_templates 가 C 를 찾다 KeyError 로 터진다.
    short = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=["KRW-BTC"],
                      period_preset="1m", interval="15m", exchange="upbit")
    presets = ask._presets_for(short)
    assert set(ask.ALL_RULE_TYPES) <= set(presets), "여덟 방식이 다 살아 있어야 한다"
    assert presets["A"] is ask._SHORT_BAR_PRESETS["A"], "짧은 봉 표가 있는 방식은 그 값을"
    assert presets["C"] is ask._PRESETS["C"], "없는 방식(C·H·I)은 일반 표의 값을"

    long_bars = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=["KRW-BTC"],
                          period_preset="3m", interval="4h", exchange="upbit")
    assert ask._presets_for(long_bars) is ask._PRESETS


def test_short_bar_templates_build_for_all_eight_types():
    short = ask._Plan(risk_profile="balanced", market="spot", leverage=1, symbols=["KRW-BTC"],
                      period_preset="1m", interval="15m", exchange="upbit",
                      account_balance=1_000_000.0)
    kinds = {c.macro.rule_type.value for c in ask.build_templates(short)}
    assert kinds == set(ask.ALL_RULE_TYPES)


def test_no_scalper_left_in_the_module():
    assert "scalper" not in str(ask.RiskProfile)
    for name in ("_SCALPER_PRESETS", "_HORIZON_PERIOD_SHORT", "_WATCH_INTERVAL_SHORT"):
        assert not hasattr(ask, name), f"{name} 이 남아 있다"


def test_no_edge_best_carries_the_numbers_that_would_be_lost():
    # 빈 화면이 한 줄로 끝나면 "매크로가 +34.82% 를 벌었다" 는 사실이 사라진다.
    evaluated = [
        _evaluated("A", trades=5, mdd=10.0, ret=34.82, hold=38.67),
        _evaluated("J", trades=5, mdd=8.0, ret=12.0, hold=38.67),
    ]
    best = ask._no_edge_best(evaluated)
    assert best["return_pct"] == 34.82
    assert best["hold_return_pct"] == 38.67
    assert best["rule_label"], "어떤 방식이었는지도 말해 준다"


def test_no_edge_best_omits_a_hold_figure_it_does_not_have():
    # 홀딩 기준을 못 구한 결과가 1등일 수 있다. 화면이 "null%" 를 찍으면 안 된다.
    best = ask._no_edge_best([_evaluated("A", trades=5, mdd=10.0, ret=5.0, hold=None)])
    assert best["return_pct"] == 5.0
    assert best["hold_return_pct"] is None
    assert ask._no_edge_best([]) is None
