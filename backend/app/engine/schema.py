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


# --- 진입 필터 --------------------------------------------------------
# 기존 규칙의 새 진입에만 걸리는 관문 하나. 스스로 사거나 팔지 않는다.
class FilterKind(str, enum.Enum):
    MA = "ma"            # 종가가 이동평균 위/아래
    RSI = "rsi"          # RSI 가 구간 안
    BOLLINGER = "bb"     # 볼린저 밴드 기준 위치
    VOLUME = "volume"    # 거래량이 평균의 N배 이상


class MAFilterParams(BaseModel):
    ma_type: Literal["SMA", "EMA"] = "SMA"
    period: int = Field(ge=2, le=400)      # WARMUP_CANDLES=500 이 덮는 상한
    side: Literal["above", "below"]


class RSIFilterParams(BaseModel):
    period: int = Field(default=14, ge=2, le=200)
    min: Optional[float] = Field(default=None, ge=0, le=100)
    max: Optional[float] = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> "RSIFilterParams":
        if self.min is None and self.max is None:
            raise ValueError("rsi filter requires min or max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("rsi filter min must not exceed max")
        return self


class BollingerFilterParams(BaseModel):
    period: int = Field(default=20, ge=2, le=400)
    num_std: float = Field(default=2.0, gt=0, le=5)
    zone: Literal["below_lower", "above_upper", "inside"]


class VolumeFilterParams(BaseModel):
    period: int = Field(default=20, ge=2, le=400)
    multiple: float = Field(gt=0, le=100)


_FILTER_PARAMS_MODEL: dict[FilterKind, type[BaseModel]] = {
    FilterKind.MA: MAFilterParams,
    FilterKind.RSI: RSIFilterParams,
    FilterKind.BOLLINGER: BollingerFilterParams,
    FilterKind.VOLUME: VolumeFilterParams,
}


class EntryFilter(BaseModel):
    kind: FilterKind
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate(self) -> "EntryFilter":
        # Macro._validate_new_type 과 같은 모양 — kind 별 모델로 다시 검증하고
        # model_dump() 로 정규화해 되담는다(기본값 적용·모르는 키 탈락).
        self.params = _FILTER_PARAMS_MODEL[self.kind](**self.params).model_dump()
        return self


# 실시간에서 캔들을 받는 규칙만 필터를 걸 수 있다. A·B(PositionSim)·C(DcaSim) 는 틱 기반이라
# 지표를 계산할 데이터가 없고, D 그리드는 사다리 중간을 막으면 팔 짝 없는 매수가 남는다.
FILTERABLE_TYPES = frozenset({
    RuleType.E, RuleType.F, RuleType.G, RuleType.H, RuleType.I, RuleType.J, RuleType.K,
})

# 진입 관문(진입 필터 · 묶음 한도)을 안전하게 끼울 수 있는 규칙. 같은 집합을 두 뜻으로
# 쓰므로 이름을 따로 둔다 — D(그리드)는 사다리 한 칸을 막으면 짝 없는 매수가 남고,
# A·B(틱 구동)·C(적립식)는 봉 관문을 지나지 않는다.
GATEABLE_TYPES = FILTERABLE_TYPES


class PortfolioLeg(BaseModel):
    """묶음의 한 다리. 규칙 관련 칸이 None 이면 묶음의 기본값을 쓴다."""

    symbol: str
    weight: float = Field(gt=0, le=100)      # 묶음 초기자금 중 이 레그의 몫(%)
    rule_type: Optional[RuleType] = None
    params: Optional[dict] = None            # rule_type 을 바꿨으면 반드시 같이 준다
    entry_filter: Optional[EntryFilter] = None


class BundleRisk(BaseModel):
    """묶음 전체에 거는 한도. 레그 자금 분배(비중)와는 다른 것이다."""

    # 동시에 포지션을 들고 있을 수 있는 종목 수.
    max_positions: Optional[int] = Field(default=None, ge=1, le=5)
    # 투입 자본(margin — 레버리지 걸린 명목금액이 아니다) 합이 묶음 초기자금의 몇 %까지 갈 수 있는가.
    max_exposure_pct: Optional[float] = Field(default=None, gt=0, le=100)

    @model_validator(mode="after")
    def _check(self) -> "BundleRisk":
        if self.max_positions is None and self.max_exposure_pct is None:
            raise ValueError("묶음 한도는 최소 하나를 정해야 합니다")
        return self


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
    entry_filter: Optional[EntryFilter] = None
    # 묶음(포트폴리오) — 종목별 비중과 규칙 덮어쓰기. symbols 와 함께 쓸 수 없다.
    legs: Optional[list[PortfolioLeg]] = None
    bundle_risk: Optional[BundleRisk] = None
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

        if self.legs is not None:
            if self.symbols:
                raise ValueError("symbols 와 legs 는 함께 쓸 수 없습니다")
            if len(self.legs) < 2:
                raise ValueError("묶음은 종목 2개 이상이어야 합니다")
            if len(self.legs) > self.MAX_SYMBOLS:
                raise ValueError(f"묶음은 종목 최대 {self.MAX_SYMBOLS}개까지예요")
            seen_legs: list[str] = []
            for leg in self.legs:
                leg.symbol = validate_symbol(leg.symbol, self.exchange)
                if leg.symbol in seen_legs:
                    raise ValueError(f"같은 종목을 두 번 넣었어요: {leg.symbol}")
                seen_legs.append(leg.symbol)
            total = sum(leg.weight for leg in self.legs)
            if abs(total - 100.0) > 0.01:
                raise ValueError(f"비중의 합이 100% 여야 합니다 (지금 {total:g}%)")
            # 첫 레그가 대표 종목 — 슬러그 · 요약 · 단일 종목 경로가 그대로 돈다.
            self.symbol = self.legs[0].symbol

        if self.bundle_risk is not None and not self.is_portfolio():
            raise ValueError("묶음 한도는 종목 2개 이상에서만 쓸 수 있습니다")

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

        if self.entry_filter is not None and self.rule_type not in FILTERABLE_TYPES:
            raise ValueError(
                f"rule_type {self.rule_type.value} does not support entry_filter"
            )

        if self.rule_type in _PARAMS_MODEL:
            self._validate_new_type()
        else:
            self._validate_legacy_type()

        # 묶음 한도 검사는 `is_portfolio()` 로 그대로 둔다 — `bundle_risk` 는 이 브랜치가
        # 만든 새 칸이라 기존 매크로에는 없다. 거절이 늘어날 일이 없다.
        if self.is_portfolio():
            specs = self.leg_specs()
            if self.bundle_risk is not None:
                for leg in specs:
                    rule = self.leg_rule(leg)
                    if rule not in GATEABLE_TYPES:
                        raise ValueError(
                            f"묶음 한도는 규칙 {rule.value} 에 쓸 수 없습니다 — E~K 만 지원해요"
                        )
                cap = self.bundle_risk.max_positions
                if cap is not None and cap >= len(specs):
                    raise ValueError("동시 보유 상한이 종목 수보다 작아야 의미가 있어요")

        # 레그 검증은 묶음 본체의 params 검증 **뒤**다. 앞에 두면 본체 params 가 잘못됐을 때
        # 오류가 "레그 X 설정을 확인해 주세요" 로 나와 엉뚱한 곳을 가리킨다. 또 레그는
        # 정규화를 마친 본체 params 를 물려받는 편이 맞다.
        #
        # 레그 펼치기 검증은 비중을 **명시한** 묶음(`legs`)에만 걸린다. `symbols` 형태는
        # 비중을 적은 적이 없으므로 레그 몫 자금으로 검사하면, 분기점에서 저장을 통과했던
        # 매크로가 이제 거절된다 — 이미 저장된 그런 매크로는 조회가 터져 공유 링크가 열리지
        # 않고 사용자가 고칠 길도 없다. `symbols` 형태에서 레그 몫 자금이 모자란 것은
        # 백테스트 때 드러난다(분기점과 같은 동작이다). 문구도 거짓이 된다 — "레그" 라는
        # 말을 쓴 적 없는 사용자에게 "레그 BTCUSDT 설정을 확인해 주세요" 가 나간다.
        if self.legs:
            specs = self.leg_specs()
            base = self.initial_capital
            for leg in specs:
                if leg.rule_type is not None and leg.rule_type is not self.rule_type \
                        and leg.params is None:
                    raise ValueError(f"레그 {leg.symbol}: 규칙을 바꾸면 세부값도 함께 주세요")
                # 레그를 실제로 펼쳐 Macro 검증기를 통째로 돌린다 — 규칙별 params, 진입 조건
                # 적용 가능 여부, 국내 거래소 제약, 레버리지까지 한 번에 본다. 레그 검증
                # 로직을 두 벌로 베끼면 둘이 어긋나는 날이 온다.
                #
                # 자본은 **레그가 실제로 받는 몫**(묶음 자금 × 비중)으로 준다. 묶음 전체 자금으로
                # 검증하면 자금 사전검사(_validate_new_type)를 넉넉히 넘기는 설정이 저장되고,
                # 백테스트가 레그 몫으로 펼칠 때 pydantic 예외가 그대로 올라간다.
                per_cap = (base * leg.weight / 100.0) if base else None
                try:
                    self.for_leg(leg, per_cap)
                except ValueError as exc:
                    raise ValueError(f"레그 {leg.symbol} 설정을 확인해 주세요") from exc

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
        if self.legs:
            return [leg.symbol for leg in self.legs]
        return self.symbols if self.symbols else [self.symbol]

    def is_portfolio(self) -> bool:
        return bool(self.legs) or (bool(self.symbols) and len(self.symbols) > 1)

    def for_symbol(self, symbol: str, initial_capital: Optional[float] = None) -> "Macro":
        """A single-symbol copy for one leg of a portfolio (optionally re-capitalized)."""
        data = self.model_dump()
        data["symbol"] = symbol
        data["symbols"] = None
        if initial_capital is not None and data.get("params", {}).get("initial_capital") is not None:
            data["params"] = {**data["params"], "initial_capital": initial_capital}
        return Macro(**data)

    def leg_specs(self) -> list["PortfolioLeg"]:
        """묶음을 레그 목록으로 정규화한다. symbols 형태는 균등 비중으로 바꿔 돌려준다.

        단일 종목 매크로는 빈 목록 — 호출부가 "묶음이 아니다" 로 읽는다.
        """
        if self.legs:
            return list(self.legs)
        syms = self.all_symbols()
        if len(syms) < 2:
            return []
        weight = 100.0 / len(syms)
        return [PortfolioLeg(symbol=s, weight=weight) for s in syms]

    def leg_rule(self, leg: "PortfolioLeg") -> RuleType:
        return leg.rule_type if leg.rule_type is not None else self.rule_type

    def leg_filter(self, leg: "PortfolioLeg") -> Optional[EntryFilter]:
        """레그의 실효 진입 조건.

        레그가 규칙을 바꿨으면 묶음의 조건은 물려받지 않는다 — 그 조건은 다른 규칙을 위해
        쓴 것이고, 조용히 엉뚱한 관문이 붙는 것이 사용자에게 가장 설명하기 어려운 결과다.
        """
        if leg.entry_filter is not None:
            return leg.entry_filter
        if leg.rule_type is not None and leg.rule_type is not self.rule_type:
            return None
        return self.entry_filter

    def for_leg(self, leg: "PortfolioLeg", initial_capital: Optional[float] = None) -> "Macro":
        """레그 하나를 단일 종목 매크로로 펼친다. 묶음 칸은 지워서 돌려준다."""
        data = self.model_dump()
        data["symbol"] = leg.symbol
        data["symbols"] = None
        data["legs"] = None
        data["bundle_risk"] = None
        data["rule_type"] = self.leg_rule(leg)
        if leg.params is not None:
            data["params"] = dict(leg.params)
        eff_filter = self.leg_filter(leg)
        data["entry_filter"] = eff_filter.model_dump() if eff_filter is not None else None
        if initial_capital is not None:
            params = dict(data.get("params") or {})
            if params.get("initial_capital") is not None:
                params["initial_capital"] = initial_capital
                data["params"] = params
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
