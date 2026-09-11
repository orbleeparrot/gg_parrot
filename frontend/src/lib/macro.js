// Rule metadata + form<->macro-JSON mapping (mirrors the backend schema).
//
// A/B/C are the original strategies (unchanged). D~J are the v4 additions; each
// carries its own params block (see TYPE_DEFAULTS / buildParams) and shares the
// common envelope (candle_interval + advanced risk). Field names match the
// backend pydantic models exactly so clone (macroToForm) is a direct Object.assign.

// Demo safety cap on leverage (mirrors backend MAX_LEVERAGE default). Leverage is
// a backtest/paper-only condition — C (DCA) is excluded and forced to 1x.
export const MAX_LEVERAGE = 20;

export const RULE_TYPES = {
  A: { label: "A · 익절/손절 후 재진입", allowShort: true },
  B: { label: "B · 지정가 밴드 매매", allowShort: true },
  C: { label: "C · 정기 분할매수(DCA, 롱 전용)", allowShort: false },
  D: { label: "D · 그리드 매매", allowShort: false },
  E: { label: "E · 트레일링 스탑", allowShort: false },
  F: { label: "F · RSI 조건 매매", allowShort: true, indicator: true },
  G: { label: "G · 볼린저밴드", allowShort: true, indicator: true },
  H: { label: "H · 마틴게일 / 세이프티오더", allowShort: false },
  I: { label: "I · 변동성 돌파 (래리 윌리엄스)", allowShort: false },
  J: { label: "J · 이동평균 크로스", allowShort: true, indicator: true },
  K: { label: "K · 하락 방어 전환 (SAR, 선물)", allowShort: false },
};

export const PERIOD_PRESETS = [
  { value: "1y", label: "최근 1년" },
  { value: "6m", label: "최근 6개월" },
  { value: "3m", label: "최근 3개월" },
  { value: "1m", label: "최근 1개월" },
  { value: "1w", label: "최근 1주" },
  { value: "1d", label: "최근 1일" },
  { value: "custom", label: "직접 지정" },
];

export const CANDLE_INTERVALS = [
  { value: "1m", label: "1분" },
  { value: "5m", label: "5분" },
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
  { value: "1d", label: "1일" },
];

// Per-type default params (form keys == backend param keys).
export const TYPE_DEFAULTS = {
  D: {
    lower_price: 50000, upper_price: 70000, grid_count: 20, grid_mode: "arithmetic",
    per_grid_invest: "", band_exit_action: "stop", rebalance_on_start: true,
    initial_capital: 1000000,
  },
  E: {
    entry_mode: "immediate", entry_dip: 3, activation_profit: 5, trail_percent: 3,
    reenter_after_exit: true, initial_capital: 1000000,
  },
  F: {
    rsi_period: 14, entry_threshold: 30, exit_threshold: 70, confirm_candles: 1,
    exit_mode: "indicator", take_profit: "", initial_capital: 1000000,
  },
  G: {
    bb_period: 20, bb_std: 2.0, strategy: "reversion", exit_target: "mid",
    squeeze_filter: false, squeeze_lookback: 50, initial_capital: 1000000,
  },
  H: {
    base_order_size: 100000, safety_order_size: 200000, price_deviation: 2,
    safety_order_step_scale: 1.5, safety_order_volume_scale: 2.0, max_safety_orders: 5,
    take_profit: 1.5, initial_capital: 1000000,
  },
  I: {
    k: 0.5, exit_mode: "next_open", trail_percent: 2, take_profit: "",
    ma_filter_period: "", session_start_hour: 9, initial_capital: 1000000,
  },
  J: {
    ma_type: "SMA", fast_period: 20, slow_period: 60, entry_signal: "golden_cross",
    exit_signal: "dead_cross", take_profit: "", confirm_candles: 1, initial_capital: 1000000,
  },
  K: {
    long_take_profit_pct: "", drop_trigger_pct: 5, partial_exit_pct: 50, flip_to_short: true,
    short_take_profit_pct: 5, short_stop_loss_pct: 3, reenter_long_after: true, initial_capital: 1000000,
  },
};

export function defaultForm() {
  return {
    symbol: "BTCUSDT",
    rule_type: "A",
    position_side: "long",
    candle_interval: "1d",
    // leverage (backtest/paper only; 1 == spot, no liquidation)
    leverage: 1,
    margin_mode: "isolated",
    // price-data source: auto(=선물 if 숏/레버리지, else 현물) | spot | futures
    market: "auto",
    // A/B/C params
    take_profit_pct: 5,
    buy_price: 55000,
    sell_price: 62000,
    initial_capital: 1000000,
    amount_per_buy: 100000,
    interval_days: 7,
    // D~J params (superset; overwritten per-type on switch/clone)
    ...TYPE_DEFAULTS.D,
    ...TYPE_DEFAULTS.E,
    ...TYPE_DEFAULTS.F,
    ...TYPE_DEFAULTS.G,
    ...TYPE_DEFAULTS.H,
    ...TYPE_DEFAULTS.I,
    ...TYPE_DEFAULTS.J,
    ...TYPE_DEFAULTS.K,
    // common risk
    invest_ratio_pct: 100,
    stop_loss_pct: 3,
    use_stop_loss: true,
    use_daily_max_loss: false,
    daily_max_loss_pct: 10,
    use_max_holding: false,
    max_holding_hours: 24,
    cooldown_minutes: 0,
    // period
    preset: "1y",
    start: "",
    end: "",
    // fees
    commission_pct: 0.1,
    slippage_pct: 0.05,
    funding_pct: 0.0,
  };
}

// Merge a type's default params in when switching rule_type (resets stale/shared keys).
export function withTypeDefaults(form, rt) {
  const next = { ...form, rule_type: rt };
  if (TYPE_DEFAULTS[rt]) Object.assign(next, TYPE_DEFAULTS[rt]);
  if (!RULE_TYPES[rt].allowShort) next.position_side = "long";
  if (rt === "C") next.leverage = 1; // DCA is leverage-excluded (1x fixed)
  return next;
}

const num = (v) => Number(v);
const optNum = (v) => (v === "" || v == null ? null : Number(v));

// H worst-case funding = base + Σ safety_i (mirrors backend ParamsH.required_funds).
function martingaleRequiredFunds(form) {
  let total = num(form.base_order_size);
  let size = num(form.safety_order_size);
  for (let i = 0; i < num(form.max_safety_orders); i++) {
    total += size;
    size *= num(form.safety_order_volume_scale);
  }
  return total;
}

export function validate(form) {
  const rt = form.rule_type;
  const meta = RULE_TYPES[rt];
  const isShort = form.position_side === "short";

  // Short A/B must set a stop loss (short loss is theoretically unbounded).
  if (isShort && (rt === "A" || rt === "B") && (!form.use_stop_loss || !(form.stop_loss_pct > 0))) {
    return "숏으로 A·B 전략을 쓸 때는 손절 기준을 반드시 입력해야 해요.";
  }
  if (isShort && !meta.allowShort) {
    return `${rt} 전략은 숏을 지원하지 않아요.`;
  }
  const lev = num(form.leverage);
  if (rt === "C" && lev > 1) return "C 분할매수 전략은 레버리지 없이 1배로만 쓸 수 있어요.";
  if (!(lev >= 1) || lev > MAX_LEVERAGE) return `레버리지는 1~${MAX_LEVERAGE}배 사이로 입력해요.`;
  if (!Number.isInteger(lev)) return "레버리지는 정수로 입력해요.";
  if (rt === "D") {
    if (!(num(form.upper_price) > num(form.lower_price))) return "D 전략의 가격 범위 상단은 하단보다 커야 해요.";
    if (form.grid_mode === "geometric" && !(num(form.lower_price) > 0)) return "D 전략에서 같은 비율 간격을 쓰려면 하단 가격이 0보다 커야 해요.";
    if (form.per_grid_invest !== "" && num(form.per_grid_invest) * num(form.grid_count) > num(form.initial_capital) * (num(form.invest_ratio_pct) / 100) + 1e-6) {
      return "D 전략의 전체 칸에 필요한 금액이 사용할 수 있는 자금을 넘어요.";
    }
  }
  if (rt === "H") {
    const budget = num(form.initial_capital) * (num(form.invest_ratio_pct) / 100);
    if (martingaleRequiredFunds(form) > budget + 1e-6) {
      return "H 전략의 최대 추가매수 금액이 사용할 수 있는 자금을 넘어요.";
    }
  }
  if (rt === "F" && (form.exit_mode === "take_profit" || form.exit_mode === "both") && !(num(form.take_profit) > 0)) {
    return "F 전략에서 익절 기준을 포함하려면 익절률을 입력해요.";
  }
  if (rt === "I" && form.exit_mode === "take_profit" && !(num(form.take_profit) > 0)) {
    return "I 전략에서 익절 기준을 골랐다면 익절률을 입력해요.";
  }
  if (rt === "J") {
    if (!(num(form.fast_period) < num(form.slow_period))) return "J 전략의 짧은 이동평균 기간은 긴 기간보다 작아야 해요.";
    if ((form.exit_signal === "take_profit" || form.exit_signal === "both") && !(num(form.take_profit) > 0)) {
      return "J 전략에서 익절 기준을 포함하려면 익절률을 입력해요.";
    }
  }
  if (rt === "K") {
    if (!(num(form.drop_trigger_pct) > 0)) return "K 전략의 방어 시작 하락폭을 입력해요.";
    if (!(num(form.partial_exit_pct) > 0 && num(form.partial_exit_pct) <= 100)) return "K 전략에서 팔 비율은 0%보다 크고 100% 이하여야 해요.";
    if (!(num(form.short_take_profit_pct) > 0)) return "K 전략의 숏 익절 기준을 입력해요.";
    if (!(num(form.short_stop_loss_pct) > 0)) return "K 전략에서 숏으로 전환하려면 손절 기준이 필요해요.";
  }
  return null;
}

function buildParams(rt, form) {
  switch (rt) {
    case "A":
      return { take_profit_pct: num(form.take_profit_pct), initial_capital: num(form.initial_capital) };
    case "B":
      return { buy_price: num(form.buy_price), sell_price: num(form.sell_price), initial_capital: num(form.initial_capital) };
    case "C":
      return { amount_per_buy: num(form.amount_per_buy), interval_days: num(form.interval_days) };
    case "D":
      return {
        lower_price: num(form.lower_price), upper_price: num(form.upper_price), grid_count: num(form.grid_count),
        grid_mode: form.grid_mode, per_grid_invest: optNum(form.per_grid_invest),
        band_exit_action: form.band_exit_action, rebalance_on_start: !!form.rebalance_on_start,
        initial_capital: num(form.initial_capital),
      };
    case "E":
      return {
        entry_mode: form.entry_mode, entry_dip: num(form.entry_dip), activation_profit: num(form.activation_profit),
        trail_percent: num(form.trail_percent), reenter_after_exit: !!form.reenter_after_exit,
        initial_capital: num(form.initial_capital),
      };
    case "F":
      return {
        rsi_period: num(form.rsi_period), entry_threshold: num(form.entry_threshold), exit_threshold: num(form.exit_threshold),
        confirm_candles: num(form.confirm_candles), exit_mode: form.exit_mode, take_profit: optNum(form.take_profit),
        initial_capital: num(form.initial_capital),
      };
    case "G":
      return {
        bb_period: num(form.bb_period), bb_std: num(form.bb_std), strategy: form.strategy, exit_target: form.exit_target,
        squeeze_filter: !!form.squeeze_filter, squeeze_lookback: num(form.squeeze_lookback),
        initial_capital: num(form.initial_capital),
      };
    case "H":
      return {
        base_order_size: num(form.base_order_size), safety_order_size: num(form.safety_order_size),
        price_deviation: num(form.price_deviation), safety_order_step_scale: num(form.safety_order_step_scale),
        safety_order_volume_scale: num(form.safety_order_volume_scale), max_safety_orders: num(form.max_safety_orders),
        take_profit: num(form.take_profit), initial_capital: num(form.initial_capital),
      };
    case "I":
      return {
        k: num(form.k), exit_mode: form.exit_mode, trail_percent: num(form.trail_percent),
        take_profit: optNum(form.take_profit), ma_filter_period: optNum(form.ma_filter_period),
        session_start_hour: num(form.session_start_hour), initial_capital: num(form.initial_capital),
      };
    case "J":
      return {
        ma_type: form.ma_type, fast_period: num(form.fast_period), slow_period: num(form.slow_period),
        entry_signal: form.entry_signal, exit_signal: form.exit_signal, take_profit: optNum(form.take_profit),
        confirm_candles: num(form.confirm_candles), initial_capital: num(form.initial_capital),
      };
    case "K":
      return {
        long_take_profit_pct: optNum(form.long_take_profit_pct), drop_trigger_pct: num(form.drop_trigger_pct),
        partial_exit_pct: num(form.partial_exit_pct), flip_to_short: !!form.flip_to_short,
        short_take_profit_pct: num(form.short_take_profit_pct), short_stop_loss_pct: num(form.short_stop_loss_pct),
        reenter_long_after: !!form.reenter_long_after, initial_capital: num(form.initial_capital),
      };
    default:
      return {};
  }
}

export function buildMacro(form) {
  const rt = form.rule_type;
  const meta = RULE_TYPES[rt];
  const useSL = form.use_stop_loss && form.stop_loss_pct > 0;
  // 멀티종목(포트폴리오): 쉼표로 여러 종목 입력 -> symbols 배열. 하나면 단일.
  const syms = (form.symbol || "")
    .split(",")
    .map((s) => s.trim().toUpperCase())
    .filter(Boolean);
  const uniq = [...new Set(syms)];
  return {
    symbol: uniq[0] || "BTCUSDT",
    symbols: uniq.length > 1 ? uniq.slice(0, 5) : null,
    rule_type: rt,
    position_side: rt === "C" || !meta.allowShort ? "long" : form.position_side,
    candle_interval: form.candle_interval || "1d",
    leverage: rt === "C" ? 1 : Math.max(1, Math.round(num(form.leverage) || 1)),
    margin_mode: "isolated",
    market: form.market || "auto",
    params: buildParams(rt, form),
    risk: {
      invest_ratio: num(form.invest_ratio_pct) / 100,
      stop_loss_pct: useSL ? num(form.stop_loss_pct) : null,
      daily_max_loss_pct: form.use_daily_max_loss && form.daily_max_loss_pct > 0 ? num(form.daily_max_loss_pct) : null,
      max_holding_hours: form.use_max_holding && form.max_holding_hours > 0 ? num(form.max_holding_hours) : null,
      cooldown_minutes: num(form.cooldown_minutes) || 0,
    },
    period: {
      preset: form.preset,
      start: form.preset === "custom" ? form.start : null,
      end: form.preset === "custom" ? form.end : null,
    },
    fees: {
      commission_pct: num(form.commission_pct),
      slippage_pct: num(form.slippage_pct),
      funding_pct: num(form.funding_pct),
    },
  };
}

// Load a stored macro JSON back into editable form state (clone flow).
export function macroToForm(macro) {
  const f = defaultForm();
  f.symbol = macro.symbols && macro.symbols.length > 1 ? macro.symbols.join(", ") : macro.symbol;
  f.rule_type = macro.rule_type;
  f.position_side = macro.position_side;
  f.candle_interval = macro.candle_interval ?? "1d";
  f.leverage = macro.leverage ?? 1;
  f.margin_mode = macro.margin_mode ?? "isolated";
  f.market = macro.market ?? "auto";
  Object.assign(f, macro.params); // param keys == form keys
  // null per_grid_invest / take_profit / ma_filter_period -> empty input
  ["per_grid_invest", "take_profit", "ma_filter_period", "long_take_profit_pct"].forEach((k) => {
    if (f[k] == null) f[k] = "";
  });
  const r = macro.risk || {};
  f.invest_ratio_pct = Math.round((r.invest_ratio ?? 1) * 100);
  f.use_stop_loss = r.stop_loss_pct != null;
  f.stop_loss_pct = r.stop_loss_pct ?? 3;
  f.use_daily_max_loss = r.daily_max_loss_pct != null;
  f.daily_max_loss_pct = r.daily_max_loss_pct ?? 10;
  f.use_max_holding = r.max_holding_hours != null;
  f.max_holding_hours = r.max_holding_hours ?? 24;
  f.cooldown_minutes = r.cooldown_minutes ?? 0;
  f.preset = macro.period?.preset ?? "1y";
  f.start = macro.period?.start ?? "";
  f.end = macro.period?.end ?? "";
  f.commission_pct = macro.fees?.commission_pct ?? 0.1;
  f.slippage_pct = macro.fees?.slippage_pct ?? 0.05;
  f.funding_pct = macro.fees?.funding_pct ?? 0.0;
  return f;
}
