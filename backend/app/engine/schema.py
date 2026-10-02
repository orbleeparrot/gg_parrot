"""Normalized macro data model (storage / share / clone contract).

A macro is fully described by this JSON. The backtest engine consumes it as a
pure input; the API stores it verbatim and serves it by ``share_slug``.

Rule types A/B/C are the original single-position / DCA strategies and are kept
byte-for-byte compatible. Types D~J are added as a discriminated union keyed on
``rule_type``: each has its own validated params model (see ``_PARAMS_MODEL``)
and runs on the shared candle engine (``engine.candles``).
"""
from __future__ import annotations

import enum
import math
import os
from typing import ClassVar, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from ..exchanges import Exchange, is_domestic, quote_currency as exchange_quote, validate_symbol

# Upper bound on macro leverage (demo safety cap). Env-tunable; the builder mirrors
# this default. Leverage is a backtest/paper-only concept (never applied to real
# trading), and only directional types use it — C (DCA) is forced to 1.
MAX_LEVERAGE = int(os.environ.get("MAX_LEVERAGE", "20"))


class RuleType(str, enum.Enum):
    A = "A"  # take-profit / stop-loss then re-enter
    B = "B"  # limit band trading
    C = "C"  # periodic DCA (long only)
    D = "D"  # grid trading (multi-order)
    E = "E"  # trailing stop
    F = "F"  # RSI threshold trading (indicator)
    G = "G"  # Bollinger bands (indicator)
    H = "H"  # martingale / safety orders (multi-order)
    I = "I"  # volatility breakout (Larry Williams)
    J = "J"  # moving-average cross (indicator)
    K = "K"  # SAR defense reversal (long -> partial exit -> flip short)


# Types whose signals are indicator-based -> candle_interval is meaningful and
# execution must be look-ahead safe (decide on closed bar, fill next open).
INDICATOR_TYPES = frozenset({RuleType.F, RuleType.G, RuleType.J})
# Types that place several resting orders at once -> multi_order engine mode.
MULTI_ORDER_TYPES = frozenset({RuleType.D, RuleType.H})
# Everything except the three originals runs on the candle engine.
CANDLE_TYPES = frozenset(
    {RuleType.D, RuleType.E, RuleType.F, RuleType.G, RuleType.H, RuleType.I, RuleType.J, RuleType.K}
)


class PositionSide(str, enum.Enum):
    LONG = "long"
    SHORT = "short"


class Risk(BaseModel):
    invest_ratio: float = Field(default=1.0, gt=0, le=1.0)  # fraction of equity per entry
    stop_loss_pct: Optional[float] = Field(default=None, ge=0)  # e.g. 3.0 == 3%
    # --- common advanced risk controls (all types; null/0 == disabled) ------
    daily_max_loss_pct: Optional[float] = Field(default=None, ge=0)  # halt trading for the day
    max_holding_hours: Optional[float] = Field(default=None, ge=0)  # force-close after N hours
    cooldown_minutes: float = Field(default=0.0, ge=0)  # block re-entry after a stop-loss


class Period(BaseModel):
    preset: Optional[str] = "1y"  # 1y | 6m | 3m | custom
    start: Optional[str] = None  # ISO date, used when preset == custom
    end: Optional[str] = None


class Fees(BaseModel):
    commission_pct: float = Field(default=0.1, ge=0)  # per side, percent
    slippage_pct: float = Field(default=0.05, ge=0)  # per fill, percent
    funding_pct: float = Field(default=0.0, ge=0)  # per day for shorts, percent


# --- per-type params models (D~J) --------------------------------------
# These mirror the frontend builder. Each includes ``initial_capital`` so the
# existing ``Macro.initial_capital`` accessor, slug logic and paper sizing keep
# working uniformly. Field names follow the v4 spec verbatim.
class ParamsD(BaseModel):
    """Grid trading."""

    lower_price: float = Field(gt=0)
    upper_price: float = Field(gt=0)
    grid_count: int = Field(ge=2, le=200)
    grid_mode: Literal["arithmetic", "geometric"] = "arithmetic"
    per_grid_invest: Optional[float] = Field(default=None, gt=0)
    band_exit_action: Literal["stop", "hold"] = "stop"
    rebalance_on_start: bool = True
    initial_capital: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> "ParamsD":
        if self.upper_price <= self.lower_price:
            raise ValueError("D: upper_price must be greater than lower_price")
        return self


class ParamsE(BaseModel):
    """Trailing stop."""

    entry_mode: Literal["immediate", "dip"] = "immediate"
    entry_dip: float = Field(default=3.0, ge=0)  # percent, used when entry_mode == dip
    activation_profit: float = Field(default=5.0, ge=0)  # arm the trail after +X%
    trail_percent: float = Field(gt=0)  # give-back that triggers exit
    reenter_after_exit: bool = True
    initial_capital: float = Field(gt=0)


class ParamsF(BaseModel):
    """RSI threshold trading."""

    rsi_period: int = Field(default=14, ge=2, le=200)
    entry_threshold: float = Field(default=30.0, ge=0, le=100)
    exit_threshold: float = Field(default=70.0, ge=0, le=100)
    confirm_candles: int = Field(default=1, ge=1, le=10)
    exit_mode: Literal["indicator", "take_profit", "both"] = "indicator"
    take_profit: Optional[float] = Field(default=None, gt=0)
    initial_capital: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> "ParamsF":
        if self.exit_mode in ("take_profit", "both") and self.take_profit is None:
            raise ValueError("F: exit_mode take_profit/both requires take_profit")
        return self


class ParamsG(BaseModel):
    """Bollinger bands."""

    bb_period: int = Field(default=20, ge=2, le=200)
    bb_std: float = Field(default=2.0, gt=0)
    strategy: Literal["reversion", "breakout"] = "reversion"
    exit_target: Literal["mid", "opposite"] = "mid"
    squeeze_filter: bool = False
    squeeze_lookback: int = Field(default=50, ge=2, le=500)
    initial_capital: float = Field(gt=0)


class ParamsH(BaseModel):
    """Martingale / safety orders (DCA-into-loss)."""

    base_order_size: float = Field(gt=0)
    safety_order_size: float = Field(gt=0)
    price_deviation: float = Field(gt=0)  # percent step between safety orders
    safety_order_step_scale: float = Field(default=1.0, gt=0)
    safety_order_volume_scale: float = Field(default=1.0, gt=0)
    max_safety_orders: int = Field(default=5, ge=0, le=50)
    take_profit: float = Field(gt=0)  # percent above average entry
    initial_capital: float = Field(gt=0)

    def required_funds(self) -> float:
        """Worst case: base order + every safety order filled."""
        total = self.base_order_size
        size = self.safety_order_size
        for _ in range(self.max_safety_orders):
            total += size
            size *= self.safety_order_volume_scale
        return total


class ParamsI(BaseModel):
    """Volatility breakout (Larry Williams)."""

    k: float = Field(default=0.5, gt=0, le=2.0)
    exit_mode: Literal["next_open", "trailing", "take_profit"] = "next_open"
    trail_percent: float = Field(default=2.0, gt=0)
    take_profit: Optional[float] = Field(default=None, gt=0)
    ma_filter_period: Optional[int] = Field(default=None, ge=2, le=200)
    session_start_hour: int = Field(default=9, ge=0, le=23)
    initial_capital: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> "ParamsI":
        if self.exit_mode == "take_profit" and self.take_profit is None:
            raise ValueError("I: exit_mode take_profit requires take_profit")
        return self


class ParamsJ(BaseModel):
    """Moving-average cross."""

    ma_type: Literal["SMA", "EMA"] = "SMA"
    fast_period: int = Field(ge=1, le=400)
    slow_period: int = Field(ge=2, le=400)
    entry_signal: Literal["golden_cross"] = "golden_cross"
    exit_signal: Literal["dead_cross", "take_profit", "both"] = "dead_cross"
    take_profit: Optional[float] = Field(default=None, gt=0)
    confirm_candles: int = Field(default=1, ge=1, le=10)
    initial_capital: float = Field(gt=0)

    @model_validator(mode="after")
    def _check(self) -> "ParamsJ":
        if self.fast_period >= self.slow_period:
            raise ValueError("J: fast_period must be less than slow_period")
        if self.exit_signal in ("take_profit", "both") and self.take_profit is None:
            raise ValueError("J: exit_signal take_profit/both requires take_profit")
        return self


class ParamsK(BaseModel):
    """SAR defense reversal.

    Long first. When the long is down ``drop_trigger_pct`` from its average entry,
    sell ``partial_exit_pct`` of it (de-risk); if ``flip_to_short`` the remaining
    long is closed and a short is opened. The short exits on its own take-profit
    (further drop) or stop-loss (bounce — mandatory, short loss is unbounded).
    """

    long_take_profit_pct: Optional[float] = Field(default=None, gt=0)  # optional long TP
    drop_trigger_pct: float = Field(gt=0)  # long drawdown from avg entry -> defense
    partial_exit_pct: float = Field(default=50.0, gt=0, le=100)  # sell X% of the long at trigger
    flip_to_short: bool = True  # after partial exit, close remainder and open a short
    short_take_profit_pct: float = Field(gt=0)  # short profit target (further drop)
    short_stop_loss_pct: float = Field(gt=0)  # short stop (bounce) — REQUIRED
    reenter_long_after: bool = True  # after the short closes, re-enter long (SAR cycle)
    initial_capital: float = Field(gt=0)


_PARAMS_MODEL: dict[RuleType, type[BaseModel]] = {
    RuleType.D: ParamsD,
    RuleType.E: ParamsE,
    RuleType.F: ParamsF,
    RuleType.G: ParamsG,
    RuleType.H: ParamsH,
    RuleType.I: ParamsI,
    RuleType.J: ParamsJ,
    RuleType.K: ParamsK,
}

# Required parameter keys for the original rule types (validated on the raw dict).
_REQUIRED_PARAMS: dict[RuleType, tuple[str, ...]] = {
    RuleType.A: ("take_profit_pct", "initial_capital"),
    RuleType.B: ("buy_price", "sell_price", "initial_capital"),
    RuleType.C: ("amount_per_buy", "interval_days"),
}

_VALID_INTERVALS = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})


def required_param_names(rule_type: "RuleType") -> tuple[str, ...]:
    """Params that must be present for this rule type — the schema is the source.

    Prompt builders read this instead of copying field names by hand: a new
    required field reaches the prompt on its own instead of silently failing
    every proposal.
    """
    model = _PARAMS_MODEL.get(rule_type)
    if model is None:
        return _REQUIRED_PARAMS.get(rule_type, ())
    return tuple(name for name, field in model.model_fields.items() if field.is_required())


class Macro(BaseModel):
    macro_id: Optional[str] = None
    share_slug: Optional[str] = None
    exchange: Exchange = "binance"
    quote_currency: Optional[Literal["USDT", "KRW"]] = None
    symbol: str = "BTCUSDT"
    # Multi-symbol (portfolio) backtest: run the SAME rule on each symbol with the
    # capital split evenly, then aggregate. None/[]/single => normal single-symbol.
    symbols: Optional[list[str]] = None
    rule_type: RuleType
    position_side: PositionSide = PositionSide.LONG
    candle_interval: str = "1d"  # A/B/C fill on this bar; F/G/I/J compute indicators on it
    # Leverage is one macro *condition* (backtest/paper only, never real trading).
    # 1 == spot-equivalent with NO liquidation (byte-for-byte the old behaviour).
    # Any leverage > 1 turns on the isolated-margin liquidation simulation.
    leverage: int = Field(default=1, ge=1)
    margin_mode: Literal["isolated"] = "isolated"  # MVP: isolated only (cross is out of scope)
    # Price-data source for backtest/paper. "auto" mirrors the real bot's choice
    # (futures when the position is short or leverage>1, else spot); "spot"/
    # "futures" force it. Futures uses real USDT-M perp candles + funding.
    market: Literal["auto", "spot", "futures"] = "auto"
    params: dict = Field(default_factory=dict)
    risk: Risk = Field(default_factory=Risk)
    period: Period = Field(default_factory=Period)
    fees: Fees = Field(default_factory=Fees)
    created_at: Optional[str] = None

    # Demo cap on how many symbols one portfolio macro may span.
    MAX_SYMBOLS: ClassVar[int] = 5

    @model_validator(mode="after")
    def _validate(self) -> "Macro":
        expected_quote = exchange_quote(self.exchange)
        if self.quote_currency is not None and self.quote_currency != expected_quote:
            raise ValueError("quote_currency must match the selected exchange")
        self.quote_currency = expected_quote
        normalized_symbol = validate_symbol(self.symbol, self.exchange)
        # v1 file signatures used the exact single Binance symbol spelling.
        if is_domestic(self.exchange):
            self.symbol = normalized_symbol
        # Normalize the portfolio symbol list (upper, dedup, keep order). The
        # primary `symbol` is always the first entry so single-symbol paths and
        # slug/summary logic keep working unchanged.
        if self.symbols:
            seen: list[str] = []
            for s in self.symbols:
                su = validate_symbol(s, self.exchange)
                if su and su not in seen:
                    seen.append(su)
            if len(seen) > self.MAX_SYMBOLS:
                raise ValueError(f"portfolio supports at most {self.MAX_SYMBOLS} symbols")
            if seen:
                self.symbol = seen[0]
                self.symbols = seen if len(seen) > 1 else None
            else:
                self.symbols = None

        if is_domestic(self.exchange):
            if self.position_side is not PositionSide.LONG or self.leverage != 1:
                raise ValueError("국내 현물 매크로는 매수(long)·1배만 지원합니다")
            if self.market == "futures" or self.fees.funding_pct != 0:
                raise ValueError("국내 현물에는 선물·펀딩비를 적용할 수 없습니다")
            if self.rule_type is RuleType.K:
                raise ValueError("국내 현물에서는 공매도 전환 전략(K)을 사용할 수 없습니다")
            if self.rule_type is RuleType.C and self.candle_interval != "1d":
                raise ValueError("국내 적립식 매크로는 날짜 간격을 계산하는 일봉을 사용합니다")

        if self.rule_type is RuleType.C and "initial_capital" in self.params:
            budget = float(self.params["initial_capital"])
            if not math.isfinite(budget) or budget <= 0:
                raise ValueError("DCA initial_capital must be finite and positive")

        if self.candle_interval not in _VALID_INTERVALS:
            raise ValueError(f"candle_interval must be one of {sorted(_VALID_INTERVALS)}")

        # Rule C is long only (short DCA is out of scope).
        if self.rule_type is RuleType.C and self.position_side is not PositionSide.LONG:
            raise ValueError("rule_type C (DCA) supports long only")

        # Leverage: directional types only, capped by the demo safety limit.
        # C (DCA) is a keep-buying strategy with no single entry to liquidate.
        if self.leverage > MAX_LEVERAGE:
            raise ValueError(f"leverage must be <= {MAX_LEVERAGE}")
        if self.rule_type is RuleType.C and self.leverage != 1:
            raise ValueError("rule_type C (DCA) does not support leverage (must be 1)")

        if self.rule_type in _PARAMS_MODEL:
            self._validate_new_type()
        else:
            self._validate_legacy_type()

        return self

    # --- original A/B/C validation (unchanged behaviour) -----------------
    def _validate_legacy_type(self) -> None:
        missing = [k for k in _REQUIRED_PARAMS[self.rule_type] if k not in self.params]
        if missing:
            raise ValueError(
                f"rule_type {self.rule_type.value} requires params: {', '.join(missing)}"
            )
        # Short A/B MUST set stop_loss_pct (short loss is theoretically unbounded).
        if self.position_side is PositionSide.SHORT and self.rule_type in (RuleType.A, RuleType.B):
            if self.risk.stop_loss_pct is None or self.risk.stop_loss_pct <= 0:
                raise ValueError("short positions (rule A/B) require risk.stop_loss_pct > 0")

    # --- new D~J validation (typed params + fund pre-check) --------------
    def _validate_new_type(self) -> None:
        model_cls = _PARAMS_MODEL[self.rule_type]
        parsed = model_cls(**self.params)  # raises on bad/missing fields
        # Normalize the stored dict (apply defaults / coercions) so downstream
        # readers and storage see a canonical params object.
        self.params = parsed.model_dump()

        budget = float(self.params["initial_capital"]) * self.risk.invest_ratio

        if self.rule_type is RuleType.H:
            need = ParamsH(**self.params).required_funds()
            if need > budget + 1e-9:
                raise ValueError(
                    f"H: max safety-order funding {need:,.0f} exceeds budget "
                    f"{budget:,.0f} (initial_capital × invest_ratio)"
                )
        elif self.rule_type is RuleType.D:
            need = self._grid_required_funds(parsed)  # type: ignore[arg-type]
            if need > budget + 1e-9:
                raise ValueError(
                    f"D: filling every grid needs {need:,.0f} which exceeds budget "
                    f"{budget:,.0f} (initial_capital × invest_ratio)"
                )

    @staticmethod
    def _grid_required_funds(p: "ParamsD") -> float:
        """Capital to fill every buy grid once (per-grid amount × grid levels)."""
        per_grid = p.per_grid_invest
        if per_grid is None:
            # Even split of the whole budget across grids -> always within budget.
            return 0.0
        return per_grid * p.grid_count

    # --- convenience typed accessors -------------------------------------
    @property
    def initial_capital(self) -> Optional[float]:
        v = self.params.get("initial_capital")
        return float(v) if v is not None else None

    def all_symbols(self) -> list[str]:
        """Every symbol this macro runs on (>=1). Portfolio when len > 1."""
        return self.symbols if self.symbols else [self.symbol]

    def is_portfolio(self) -> bool:
        return bool(self.symbols) and len(self.symbols) > 1

    def for_symbol(self, symbol: str, initial_capital: Optional[float] = None) -> "Macro":
        """A single-symbol copy for one leg of a portfolio (optionally re-capitalized)."""
        data = self.model_dump()
        data["symbol"] = symbol
        data["symbols"] = None
        if initial_capital is not None and data.get("params", {}).get("initial_capital") is not None:
            data["params"] = {**data["params"], "initial_capital": initial_capital}
        return Macro(**data)

    def resolved_market(self) -> str:
        """'spot' or 'futures' for data selection.

        "auto" mirrors the real bot: short OR leverage>1 needs futures, else
        spot. An explicit "spot"/"futures" is honored as-is.
        """
        if is_domestic(self.exchange):
            return "spot"
        if self.market in ("spot", "futures"):
            return self.market
        # K flips to short mid-run, so it always needs futures (spot can't short).
        needs_futures = (
            self.position_side is PositionSide.SHORT
            or self.leverage > 1
            or self.rule_type is RuleType.K
        )
        return "futures" if needs_futures else "spot"
