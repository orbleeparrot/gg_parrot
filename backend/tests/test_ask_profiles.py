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
