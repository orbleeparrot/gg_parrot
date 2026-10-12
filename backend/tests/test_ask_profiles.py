"""성향 재설계(2026-10-12) — 상한 · 거래 수 · 봉 · 세션 이관 · 빈 화면 숫자.

왜 따로 두나: test_ask.py 는 이미 1,200줄이 넘고 끝점 흐름을 다룬다. 이 파일은 성향을
가르는 **순수 함수**들만 본다 — 값이 바뀌면 여기만 보면 된다.
"""
from __future__ import annotations

import pandas as pd

from app.engine.backtest import _buy_hold_mdd_pct, run_backtest
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
