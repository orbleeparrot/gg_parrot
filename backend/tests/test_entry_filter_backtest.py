"""진입 필터가 ``run_backtest`` 를 실제로 지나간다 — 사용자가 보는 유일한 경로.

필터 시험이 심(sim) 을 직접 돌리기만 하면 백테스트 배선(거래량 열을 읽어 넘기는 줄)이 끊겨도
전부 녹색이다. 여기서는 데이터프레임을 만들어 엔드포인트와 같은 함수를 부른다.
"""
from __future__ import annotations

import pandas as pd

from app.engine import Macro, run_backtest

BREAKOUT = {"symbol": "BTCUSDT", "rule_type": "I", "candle_interval": "1h",
            "period": {"preset": "custom"}, "params": {"k": 0.1, "initial_capital": 1000},
            "risk": {"invest_ratio": 1.0}, "fees": {"commission_pct": 0, "slippage_pct": 0}}
VOLUME_SURGE = {"kind": "volume", "params": {"period": 3, "multiple": 2.0}}
# 한 칸씩 오르내리는 톱니 — 돌파(I)가 매 봉 성립해 필터가 없으면 쉬지 않고 거래한다.
CLOSES = [100.0 + (5.0 if i % 2 else 0.0) for i in range(24)]


def _df(closes, volumes=None):
    ts = pd.date_range("2026-01-01", periods=len(closes), freq="h")
    frame = {"timestamp": ts, "open": closes,
             "high": [c * 1.02 for c in closes], "low": [c * 0.98 for c in closes], "close": closes}
    if volumes is not None:
        frame["volume"] = volumes
    return pd.DataFrame(frame)


def _trades(entry_filter, volumes):
    body = dict(BREAKOUT)
    if entry_filter is not None:
        body["entry_filter"] = entry_filter
    return run_backtest(Macro(**body), _df(CLOSES, volumes)).total_trades


def test_volume_filter_changes_the_backtest_trade_count():
    """거래량이 뒤 1/3 에서만 급증하는 열 — 필터를 걸면 거래가 줄고, 거래량 열이 없으면 0 이다."""
    volumes = [10.0] * 16 + [500.0] * 8
    bare = _trades(None, volumes)
    filtered = _trades(VOLUME_SURGE, volumes)
    blind = _trades(VOLUME_SURGE, None)      # volume 열이 없다 -> 모르면 막는다(원칙 2)
    assert bare == 22, bare                  # 필터 없이는 쉬지 않고 돈다
    assert filtered == 2, filtered           # 급증 구간에서만 들어간다 — 필터가 백테스트에서 실제로 작동한다
    assert blind == 0, blind
    assert 0 < filtered < bare               # 필터는 거래를 줄이는 쪽으로만 움직인다(원칙 1)


def test_backtest_treats_a_nan_volume_as_unknown_not_as_a_number():
    """NaN 거래량은 '모른다(None)' 다 — 아는 값으로 받으면 기준선 창을 period 봉 동안 오염시킨다.

    실시간 피드(``candle_feed._volume``)는 이미 NaN 을 None 으로 거른다. 백테스트가 그러지 않으면
    같은 불량 봉이 실거래에서는 1봉, 백테스트에서는 ``period`` 봉을 막아 두 경로의 판정이 갈라진다(원칙 4).
    NaN 봉 하나는 **그 봉만** 막고 기준선은 그대로여야 하므로, 그 봉의 거래량이 평범했을 때와
    거래 수가 같아야 한다.
    """
    nan_at_bar_4 = [10.0, 10.0, 10.0, float("nan")] + [1000.0] * 20
    ordinary_at_bar_4 = [10.0, 10.0, 10.0, 10.0] + [1000.0] * 20
    with_nan = _trades(VOLUME_SURGE, nan_at_bar_4)
    with_ordinary = _trades(VOLUME_SURGE, ordinary_at_bar_4)
    assert with_ordinary > 0, with_ordinary   # 비교 대상이 거래를 해야 이 시험이 뭔가를 증명한다
    assert with_nan == with_ordinary, (with_nan, with_ordinary)
