"""Isolated-margin leverage & liquidation approximation (backtest/paper only).

This is a SIMPLIFIED model, **not** the exact Binance formula. It exists so the
demo can let users feel — with fake money — how dangerous leverage is, so the
core property that matters is preserved and everything else is kept minimal:

Model (isolated margin only):

    margin    = the equity a position commits (the cash the user "spends")
    notional  = margin × leverage      (the size that actually moves with price)
    qty       = notional / entry_fill

Liquidation (isolated): the position is force-closed once an adverse move eats
the committed margin down to a small maintenance buffer. Approximated as

    long  : liq = entry × (1 − 1/L + corr)
    short : liq = entry × (1 + 1/L − corr)

where ``corr = maintenance_margin_rate + commission_pct/100`` nudges the trigger
slightly *earlier* (conservative). The dominant ``1/L`` term guarantees the core
property the spec demands: **higher leverage ⇒ liquidation price nearer entry**.

Real exchange liquidation prices (mark price, tiered maintenance margin, funding)
differ from this; see the README "레버리지·청산" 섹션. Leverage is NOT applied to
real trading (bot.py) — this module is used only by the backtest/paper engines.
"""
from __future__ import annotations

import os
from typing import Optional

from .schema import PositionSide

# Maintenance-margin rate: fraction of notional kept as a buffer before the whole
# margin is lost. Small (0.5%) so the 1/L term dominates. Env-tunable for demos.
MAINTENANCE_MARGIN_RATE = float(os.environ.get("MAINTENANCE_MARGIN_RATE", "0.005"))


def liquidation_price(
    entry_fill: float,
    leverage: int,
    side: PositionSide,
    *,
    commission_pct: float = 0.0,
    mmr: float = MAINTENANCE_MARGIN_RATE,
) -> Optional[float]:
    """Isolated liquidation price, or ``None`` when leverage ≤ 1 (no liquidation).

    ``entry_fill`` is the (slippage-adjusted) average entry price. For a multi-lot
    book pass the quantity-weighted average entry — with a uniform per-lot leverage
    the effective book leverage is still ``L``, so the same formula applies.
    """
    if leverage is None or leverage <= 1 or entry_fill <= 0:
        return None
    inv = 1.0 / leverage
    corr = mmr + commission_pct / 100.0
    # Never let the correction cross 1/L, which would place the liquidation price
    # on the wrong side of entry (position born already liquidated). Clamp so the
    # trigger stays just inside the raw 1/L distance.
    corr = min(corr, inv * 0.9)
    if side is PositionSide.LONG:
        return entry_fill * (1.0 - inv + corr)
    return entry_fill * (1.0 + inv - corr)
