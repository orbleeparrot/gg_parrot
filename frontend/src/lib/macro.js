// Rule metadata + form<->macro-JSON mapping (mirrors the backend schema).
//
// A/B/C are the original strategies (unchanged). D~J are the v4 additions; each
// carries its own params block (see TYPE_DEFAULTS / buildParams) and shares the
// common envelope (candle_interval + advanced risk). Field names match the
// backend pydantic models exactly so clone (macroToForm) is a direct Object.assign.
import { isDomestic, normalizeExchange, normalizeSymbolForExchange, quoteForExchange } from "./exchanges.js";
import { baseOf } from "./format.js";

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

// 서버 FILTERABLE_TYPES 와 같아야 한다 — A·B·C 는 실시간에서 캔들을 안 받고, D 그리드는
// 사다리 중간을 막으면 팔 짝 없는 매수가 남는다.
export const FILTERABLE_RULE_TYPES = Object.freeze(["E", "F", "G", "H", "I", "J", "K"]);
export const FILTER_KINDS = Object.freeze([
  { value: "ma", label: "이동평균 위/아래" },
  { value: "rsi", label: "RSI 구간" },
  { value: "bb", label: "볼린저 위치" },
  { value: "volume", label: "거래량 배수" },
]);

// 진입 필터를 사람 말 한 구로 — 서버 요약의 "진입 조건: …" 뒤 문구(backend entry_filter.py note())와 같은 말이다.
// 요약 한 줄은 맨 끝에 붙어 말줄임에 잘리므로, 카드 사양표 · 리더보드 전략 칸은 이 구를 따로 그린다. 필터가 없거나 모르는 종류면 null.
export function entryFilterPhrase(filter) {
  if (!filter || typeof filter !== "object") return null;
  const p = filter.params || {};
  const has = (v) => v !== null && v !== undefined && v !== "";
  switch (filter.kind) {
    case "ma":
      return `${p.period}봉 ${p.ma_type ?? "SMA"} 이동평균 ${p.side === "below" ? "아래" : "위"}`;
    case "rsi":
      if (has(p.min) && has(p.max)) return `RSI(${p.period}) ${Number(p.min)}~${Number(p.max)}`;
      if (has(p.max)) return `RSI(${p.period}) ${Number(p.max)} 이하`;
      if (has(p.min)) return `RSI(${p.period}) ${Number(p.min)} 이상`;
      return null;
    case "bb": {
      const zone = { below_lower: "하단 밖", above_upper: "상단 밖", inside: "밴드 안" }[p.zone ?? "inside"];
      return zone ? `볼린저(${p.period}, ${Number(p.num_std)}σ) ${zone}` : null;
    }
    case "volume":
      return `거래량이 ${p.period}봉 평균의 ${Number(p.multiple)}배 이상`;
    default:
      return null;
  }
}

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
    exchange: "binance",
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
    // 진입 필터 (E~K 만): 켜면 이 조건일 때만 새로 산다
    use_entry_filter: false, filter_kind: "ma",
    filter_ma_type: "SMA", filter_ma_period: 20, filter_ma_side: "above",
    filter_rsi_period: 14, filter_rsi_min: "", filter_rsi_max: 70,
    filter_bb_period: 20, filter_bb_num_std: 2, filter_bb_zone: "inside",
    filter_vol_period: 20, filter_vol_multiple: 2,
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
  // 필터를 못 쓰는 규칙으로 옮기면 필터를 버린다. 남겨 두면 서버가 거부하고
  // 사용자는 왜 안 되는지 알 수 없다.
  if (!FILTERABLE_RULE_TYPES.includes(rt)) next.use_entry_filter = false;
  if (isDomestic(form.exchange)) {
    next.position_side = "long";
    next.leverage = 1;
    next.market = "spot";
    next.funding_pct = 0;
    next.flip_to_short = false;
    if (rt === "C") next.candle_interval = "1d";
    // Changing strategy must not repopulate reset money fields in the wrong units.
    for (const key of EXCHANGE_MONEY_FIELDS) if (key in (TYPE_DEFAULTS[rt] || {})) next[key] = form[key] ?? "";
  }
  return next;
}

const EXCHANGE_MONEY_FIELDS = ["initial_capital", "amount_per_buy", "buy_price", "sell_price", "lower_price", "upper_price", "per_grid_invest", "base_order_size", "safety_order_size"];

export function withExchangeDefaults(form, value, items = []) {
  const exchange = normalizeExchange(value);
  if (exchange === normalizeExchange(form.exchange)) return form;
  const listed = new Set(items.map((item) => item.symbol));
  const symbol = [...new Set(String(form.symbol || "").split(",")
    .map((value) => normalizeSymbolForExchange(baseOf(value.trim()), exchange))
    .filter((value) => listed.has(value)))].join(",");
  const next = { ...form, exchange, symbol, funding_pct: 0 };
  for (const key of EXCHANGE_MONEY_FIELDS) next[key] = "";
  if (isDomestic(exchange)) {
    Object.assign(next, { position_side: "long", leverage: 1, market: "spot", flip_to_short: false });
    // K 는 국내에서 못 쓴다 — A 로 내리면서 필터도 버린다(A 는 필터를 못 쓴다). withTypeDefaults 를 거치지 않는 길이라
    // 여기서 안 끄면, 다음에 필터 규칙을 골랐을 때 버린 필터가 체크된 채 되살아난다.
    if (next.rule_type === "K") { next.rule_type = "A"; next.use_entry_filter = false; }
    if (next.rule_type === "C") next.candle_interval = "1d";
  } else next.market = "auto";
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

// 입력 검증 — 걸린 항목(form 의 키)과 문구를 함께 돌려준다. 화면은 그 칸을 노랗게 띄우고 라벨 아래에 문구를 적는다.
export function validateDetailed(form) {
  const rt = form.rule_type;
  const meta = RULE_TYPES[rt];
  const isShort = form.position_side === "short";
  const fail = (field, message) => ({ field, message });

  let exchange;
  try { exchange = normalizeExchange(form.exchange); }
  catch (_) { return fail("exchange", "지원하지 않는 거래소예요."); }
  const domestic = isDomestic(exchange);
  const symbols = String(form.symbol || "").split(",").map((value) => value.trim().toUpperCase()).filter(Boolean);
  const symbolPattern = domestic ? /^KRW-[A-Z0-9]{1,20}$/ : /^[A-Z0-9]{1,20}USDT$/;
  if (!symbols.length || symbols.length > 5 || !symbols.every((symbol) => symbolPattern.test(symbol))) return fail("symbol", domestic ? "선택한 거래소의 KRW 종목을 골라 주세요." : "바이낸스 USDT 종목을 골라 주세요.");
  if (domestic) {
    if (isShort) return fail("position_side", "업비트·빗썸 원화 현물에서는 숏을 사용할 수 없어요.");
    if (num(form.leverage) !== 1) return fail("leverage", "국내 원화 현물은 레버리지 없이 1배로만 사용해요.");
    if (form.market === "futures") return fail("market", "국내 원화 현물에서는 선물 시장을 사용할 수 없어요.");
    if (rt === "K") return fail("rule_type", "K 숏 전환 전략은 바이낸스 선물에서만 사용할 수 있어요.");
    if (rt === "C" && form.candle_interval !== "1d") return fail("candle_interval", "국내 정기 분할매수는 일봉으로만 확인해요.");
    if (num(form.funding_pct || 0) !== 0) return fail("funding_pct", "원화 현물에는 선물 펀딩비를 적용하지 않아요.");
  }
  if (!(num(form.initial_capital) > 0)) return fail("initial_capital", "시작 자금을 선택한 거래소의 통화로 다시 입력해 주세요.");
  if (rt === "C" && !(num(form.amount_per_buy) > 0)) return fail("amount_per_buy", "한 번에 살 금액을 입력해 주세요.");
  if (rt === "B") for (const key of ["buy_price", "sell_price"]) if (!(num(form[key]) > 0)) return fail(key, "선택한 거래소의 가격을 입력해 주세요.");
  if (rt === "D" && !(num(form.lower_price) > 0)) return fail("lower_price", "선택한 거래소의 가격 범위를 입력해 주세요.");
  if (rt === "H") for (const key of ["base_order_size", "safety_order_size"]) if (!(num(form[key]) > 0)) return fail(key, "선택한 거래소의 매수 금액을 입력해 주세요.");

  // Short A/B must set a stop loss (short loss is theoretically unbounded).
  if (isShort && (rt === "A" || rt === "B") && (!form.use_stop_loss || !(form.stop_loss_pct > 0))) {
    return fail("stop_loss_pct", "숏으로 A·B 전략을 쓸 때는 손절 기준을 반드시 입력해야 해요.");
  }
  if (isShort && !meta.allowShort) {
    return fail("position_side", `${rt} 전략은 숏을 지원하지 않아요.`);
  }
  const lev = num(form.leverage);
  if (rt === "C" && lev > 1) return fail("leverage", "C 분할매수 전략은 레버리지 없이 1배로만 쓸 수 있어요.");
  if (!(lev >= 1) || lev > MAX_LEVERAGE) return fail("leverage", `레버리지는 1~${MAX_LEVERAGE}배 사이로 입력해요.`);
  if (!Number.isInteger(lev)) return fail("leverage", "레버리지는 정수로 입력해요.");
  if (rt === "D") {
    if (!(num(form.upper_price) > num(form.lower_price))) return fail("upper_price", "D 전략의 가격 범위 상단은 하단보다 커야 해요.");
    if (form.grid_mode === "geometric" && !(num(form.lower_price) > 0)) return fail("lower_price", "D 전략에서 같은 비율 간격을 쓰려면 하단 가격이 0보다 커야 해요.");
    if (form.per_grid_invest !== "" && num(form.per_grid_invest) * num(form.grid_count) > num(form.initial_capital) * (num(form.invest_ratio_pct) / 100) + 1e-6) {
      return fail("per_grid_invest", "D 전략의 전체 칸에 필요한 금액이 사용할 수 있는 자금을 넘어요.");
    }
  }
  if (rt === "H") {
    const budget = num(form.initial_capital) * (num(form.invest_ratio_pct) / 100);
    if (martingaleRequiredFunds(form) > budget + 1e-6) {
      return fail("max_safety_orders", "H 전략의 최대 추가매수 금액이 사용할 수 있는 자금을 넘어요.");
    }
  }
  if (rt === "F" && (form.exit_mode === "take_profit" || form.exit_mode === "both") && !(num(form.take_profit) > 0)) {
    return fail("take_profit", "F 전략에서 익절 기준을 포함하려면 익절률을 입력해요.");
  }
  if (rt === "I" && form.exit_mode === "take_profit" && !(num(form.take_profit) > 0)) {
    return fail("take_profit", "I 전략에서 익절 기준을 골랐다면 익절률을 입력해요.");
  }
  if (rt === "J") {
    if (!(num(form.fast_period) < num(form.slow_period))) return fail("fast_period", "J 전략의 짧은 이동평균 기간은 긴 기간보다 작아야 해요.");
    if ((form.exit_signal === "take_profit" || form.exit_signal === "both") && !(num(form.take_profit) > 0)) {
      return fail("take_profit", "J 전략에서 익절 기준을 포함하려면 익절률을 입력해요.");
    }
  }
  if (rt === "K") {
    if (!(num(form.drop_trigger_pct) > 0)) return fail("drop_trigger_pct", "K 전략의 방어 시작 하락폭을 입력해요.");
    if (!(num(form.partial_exit_pct) > 0 && num(form.partial_exit_pct) <= 100)) return fail("partial_exit_pct", "K 전략에서 팔 비율은 0%보다 크고 100% 이하여야 해요.");
    if (!(num(form.short_take_profit_pct) > 0)) return fail("short_take_profit_pct", "K 전략의 숏 익절 기준을 입력해요.");
    if (!(num(form.short_stop_loss_pct) > 0)) return fail("short_stop_loss_pct", "K 전략에서 숏으로 전환하려면 손절 기준이 필요해요.");
  }
  if (form.use_entry_filter && FILTERABLE_RULE_TYPES.includes(rt)) {
    const k = form.filter_kind;
    if (k === "ma") {
      const period = num(form.filter_ma_period);
      if (!(period >= 2 && period <= 400)) return fail("filter_ma_period", "이동평균 기간은 2~400 봉이에요.");
    } else if (k === "rsi") {
      const period = num(form.filter_rsi_period);
      if (!(period >= 2 && period <= 200)) return fail("filter_rsi_period", "RSI 기간은 2~200 봉이에요.");
      const lo = optNum(form.filter_rsi_min), hi = optNum(form.filter_rsi_max);
      if (lo === null && hi === null) return fail("filter_rsi_max", "RSI 위 또는 아래 한쪽은 정해 주세요.");
      for (const [key, v] of [["filter_rsi_min", lo], ["filter_rsi_max", hi]]) {
        if (v !== null && !(v >= 0 && v <= 100)) return fail(key, "RSI 는 0~100 사이예요.");
      }
      if (lo !== null && hi !== null && lo > hi) return fail("filter_rsi_min", "RSI 아래 값이 위 값보다 클 수 없어요.");
    } else if (k === "bb") {
      const period = num(form.filter_bb_period);
      if (!(period >= 2 && period <= 400)) return fail("filter_bb_period", "볼린저 기간은 2~400 봉이에요.");
      const sd = num(form.filter_bb_num_std);
      if (!(sd > 0 && sd <= 5)) return fail("filter_bb_num_std", "표준편차 배수는 0 보다 크고 5 이하예요.");
    } else {
      const period = num(form.filter_vol_period);
      if (!(period >= 2 && period <= 400)) return fail("filter_vol_period", "거래량 평균 기간은 2~400 봉이에요.");
      const mult = num(form.filter_vol_multiple);
      if (!(mult > 0 && mult <= 100)) return fail("filter_vol_multiple", "거래량 배수는 0 보다 크고 100 이하예요.");
    }
  }
  return null;
}

// 문구만 필요한 곳(등록 모달 · 시작 화면 · 히어로 백테스트)은 이걸 쓴다.
export function validate(form) {
  return validateDetailed(form)?.message ?? null;
}

function buildParams(rt, form) {
  switch (rt) {
    case "A":
      return { take_profit_pct: num(form.take_profit_pct), initial_capital: num(form.initial_capital) };
    case "B":
      return { buy_price: num(form.buy_price), sell_price: num(form.sell_price), initial_capital: num(form.initial_capital) };
    case "C":
      return { amount_per_buy: num(form.amount_per_buy), interval_days: num(form.interval_days), initial_capital: num(form.initial_capital) };
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

// 진입 필터 조립 — 필터를 못 쓰는 규칙이거나 꺼져 있으면 null.
function buildEntryFilter(form) {
  if (!form.use_entry_filter || !FILTERABLE_RULE_TYPES.includes(form.rule_type)) return null;
  switch (form.filter_kind) {
    case "ma":
      return { kind: "ma", params: { ma_type: form.filter_ma_type, period: num(form.filter_ma_period), side: form.filter_ma_side } };
    case "rsi": {
      const params = { period: num(form.filter_rsi_period) };
      // 비워 둔 쪽은 보내지 않는다 — 서버가 null 과 '없음' 을 다르게 읽는다.
      if (optNum(form.filter_rsi_min) !== null) params.min = num(form.filter_rsi_min);
      if (optNum(form.filter_rsi_max) !== null) params.max = num(form.filter_rsi_max);
      return { kind: "rsi", params };
    }
    case "bb":
      return { kind: "bb", params: { period: num(form.filter_bb_period), num_std: num(form.filter_bb_num_std), zone: form.filter_bb_zone } };
    default:
      return { kind: "volume", params: { period: num(form.filter_vol_period), multiple: num(form.filter_vol_multiple) } };
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
    exchange: normalizeExchange(form.exchange),
    quote_currency: quoteForExchange(form.exchange),
    symbol: uniq[0] || "BTCUSDT",
    symbols: uniq.length > 1 ? uniq.slice(0, 5) : null,
    rule_type: rt,
    position_side: rt === "C" || !meta.allowShort ? "long" : form.position_side,
    candle_interval: form.candle_interval || "1d",
    leverage: rt === "C" ? 1 : Math.max(1, Math.round(num(form.leverage) || 1)),
    margin_mode: "isolated",
    market: form.market || "auto",
    params: buildParams(rt, form),
    entry_filter: buildEntryFilter(form),
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
  f.exchange = normalizeExchange(macro.exchange);
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
  const ef = macro.entry_filter;
  f.use_entry_filter = !!ef;
  if (ef) {
    f.filter_kind = ef.kind;
    const p = ef.params || {};
    if (ef.kind === "ma") Object.assign(f, { filter_ma_type: p.ma_type ?? "SMA", filter_ma_period: p.period ?? 20, filter_ma_side: p.side ?? "above" });
    else if (ef.kind === "rsi") Object.assign(f, { filter_rsi_period: p.period ?? 14, filter_rsi_min: p.min ?? "", filter_rsi_max: p.max ?? "" });
    else if (ef.kind === "bb") Object.assign(f, { filter_bb_period: p.period ?? 20, filter_bb_num_std: p.num_std ?? 2, filter_bb_zone: p.zone ?? "inside" });
    else Object.assign(f, { filter_vol_period: p.period ?? 20, filter_vol_multiple: p.multiple ?? 2 });
  }
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
