"""묶음 백테스트 — 레그를 **봉 단위로 나란히** 돌린다.

왜 따로 있나: 기존 묶음 백테스트는 레그를 하나씩 끝까지 돌린 뒤 합산한다. 그 모양에서는
"지금 몇 종목 들고 있나" 를 물을 시점이 없어 묶음 한도를 표현할 수 없다. 실시간은 이미
``StrategyDriver`` 가 레그 전부를 한 객체에 들고 있으므로, 백테스트를 동기 루프로 맞추면
백테스트와 실시간이 같은 코드로 같은 답을 낸다(설계 원칙 4).

한도가 없는 묶음은 이 모듈을 쓰지 않는다 — 기존 경로를 그대로 둬서 기존 매크로의 결과가
한 바이트도 바뀌지 않게 한다. 두 경로가 같은 답을 내는 것은 시험으로 못 박았다.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import pandas as pd

from .backtest import (
    _PERIODS_PER_YEAR, BacktestResult, EquityPoint, _buy_hold_return_pct, _iso, _metrics,
)
from .bundle import BundleGate
from .candles import make_candle_sim
from .schema import Macro

TIME_COLUMN = "timestamp"
MIN_ROWS_PER_WINDOW = 2


class _Leg:
    """동기 루프가 레그 하나에 대해 들고 있는 것 전부."""

    __slots__ = ("symbol", "sim", "capital", "rows", "cursor", "curve", "closes")

    def __init__(self, symbol: str, sim, capital: float, df: pd.DataFrame) -> None:
        self.symbol = symbol
        self.sim = sim
        self.capital = capital
        self.rows = df
        self.cursor = 0
        self.curve: List[EquityPoint] = []
        self.closes = df["close"].to_numpy(dtype=float) if len(df) else None


def _volume_at(df: pd.DataFrame, i: int) -> Optional[float]:
    """NaN · inf 는 '모른다(None)' 로 — 0.0("거래가 없었다")과 다른 값이다.

    ``backtest._run_candle_engine`` 과 ``candle_feed._volume`` 이 같은 가드를 갖고 있다.
    """
    if "volume" not in df.columns:
        return None
    v = float(df["volume"].iloc[i])
    return v if math.isfinite(v) else None


def run_bundle(macro: Macro, frames: Dict[str, pd.DataFrame]) -> List[Tuple[str, BacktestResult]]:
    """레그를 봉 단위로 나란히 돌린다. 반환값은 ``portfolio.aggregate`` 가 먹는 모양이다.

    한 시각 안에서 레그 순서는 ``macro.legs`` 에 적힌 순서다. 한도가 걸릴 때 앞 레그가
    자리를 먼저 잡는다 — 결정적이고, 사용자가 순서를 바꿔 뜻을 표현할 수 있다.
    """
    specs = macro.leg_specs()
    if not specs:
        raise ValueError("묶음이 아닌 매크로입니다")
    total = macro.initial_capital or 1_000_000.0

    gate = BundleGate(macro.bundle_risk, total) if macro.bundle_risk is not None else None
    legs: List[_Leg] = []
    for spec in specs:
        df = frames.get(spec.symbol)
        if df is None:
            raise ValueError(f"레그 {spec.symbol} 의 캔들이 없습니다")
        df = df.sort_values(TIME_COLUMN).reset_index(drop=True) if len(df) else df
        capital = total * spec.weight / 100.0
        sim = make_candle_sim(macro.for_leg(spec, capital), initial_capital=capital)
        if gate is not None:
            gate.register(sim)
            sim.bundle_gate = gate
        legs.append(_Leg(spec.symbol, sim, capital, df))

    # 모든 레그의 시각 합집합. 레그는 **자기가 가진 봉에서만** 판정한다 — 상장이 늦은 종목이
    # 없는 가격으로 거래하면 안 된다.
    timeline = sorted({pd.Timestamp(t) for leg in legs for t in leg.rows[TIME_COLUMN]}) \
        if any(len(leg.rows) for leg in legs) else []

    for t in timeline:
        for leg in legs:
            i = leg.cursor
            rows = leg.rows
            if i >= len(rows) or pd.Timestamp(rows[TIME_COLUMN].iloc[i]) != t:
                continue
            ts = t.to_pydatetime()
            close = float(rows["close"].iloc[i])
            leg.sim.on_candle(
                float(rows["open"].iloc[i]), float(rows["high"].iloc[i]),
                float(rows["low"].iloc[i]), close, ts, volume=_volume_at(rows, i),
            )
            leg.curve.append(EquityPoint(t=_iso(rows[TIME_COLUMN].iloc[i]),
                                         equity=round(leg.sim.equity(close), 4)))
            leg.cursor = i + 1

    periods = _PERIODS_PER_YEAR.get(macro.candle_interval, 365.0)
    out: List[Tuple[str, BacktestResult]] = []
    for leg in legs:
        out.append((leg.symbol, _metrics(
            leg.curve,
            leg.sim.closed_trades,
            len(leg.sim.closed_trades),
            leg.sim.initial_capital,
            same_bar_sl_bars=leg.sim.same_bar_sl,
            liquidation_count=leg.sim.liquidations,
            liquidated_loss=leg.sim.liquidated_loss,
            buy_hold_return_pct=(_buy_hold_return_pct(leg.closes)
                                 if leg.closes is not None and len(leg.closes) else None),
            periods_per_year=periods,
        )))
    return out


def split_frames_by_time(frames: Dict[str, pd.DataFrame], windows: int) -> List[Dict[str, pd.DataFrame]]:
    """레그 프레임들을 **같은 시각 경계**로 자른다.

    행 수로 자르면(``walkforward.split_frame``) 레그마다 봉 수가 달라 경계가 어긋난다 —
    묶음에서는 모든 레그가 같은 기간을 보아야 한도가 뜻을 가진다.
    """
    timeline = sorted({pd.Timestamp(t) for df in frames.values() for t in df.get(TIME_COLUMN, [])})
    if len(timeline) < MIN_ROWS_PER_WINDOW:
        return []
    count = max(1, min(int(windows), len(timeline) // MIN_ROWS_PER_WINDOW))
    size = len(timeline) // count
    bounds = []
    for i in range(count):
        lo = timeline[i * size]
        hi = None if i == count - 1 else timeline[(i + 1) * size]
        bounds.append((lo, hi))

    parts: List[Dict[str, pd.DataFrame]] = []
    for lo, hi in bounds:
        cut = {}
        for sym, df in frames.items():
            if TIME_COLUMN not in df.columns or not len(df):
                cut[sym] = df
                continue
            col = pd.to_datetime(df[TIME_COLUMN])
            mask = col >= lo if hi is None else (col >= lo) & (col < hi)
            cut[sym] = df[mask].reset_index(drop=True)
        parts.append(cut)
    return parts
