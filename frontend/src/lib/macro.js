// Rule metadata + form<->macro-JSON mapping (mirrors the backend schema).
//
// A/B/C are the original strategies (unchanged). D~J are the v4 additions; each
// carries its own params block (see TYPE_DEFAULTS / buildParams) and shares the
// common envelope (candle_interval + advanced risk). Field names match the
// backend pydantic models exactly so clone (macroToForm) is a direct Object.assign.
import { isDomestic, normalizeExchange, normalizeSymbolForExchange, quoteForExchange } from "./exchanges.js";
import { baseOf } from "./format.js";
import { isEvenWeights } from "./portfolio.js";

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
    // 묶음(여러 종목): 비중 · 레그별 규칙 · 묶음 한도. 비워 두면 전과 같이 균등 분배.
    // leg_shape 는 불러온 매크로의 모양 기억이다 — "legs" 면 비중을 손대지 않아도 legs 로 되돌린다(복제가 손실 없게).
    leg_weights: "", leg_rules: {}, leg_shape: "",
    use_bundle_risk: false, bundle_max_positions: "", bundle_max_exposure_pct: "",
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
  // 묶음 칸도 초기값으로 되돌린다. 거래소를 바꾸면 종목이 다시 짜이므로 비중 개수와 레그 규칙의
  // 종목 키가 어긋나고, K→A 로 내려가는 길에서는 "한도를 못 쓰는 규칙 + 묶음 한도"(서버가 422 로
  // 거절하는 조합)가 사용자가 아무것도 안 해도 저절로 만들어진다. 진입 조건을 지우는 것과 같은 이유다.
  Object.assign(next, {
    leg_weights: "", leg_rules: {}, leg_shape: "",
    use_bundle_risk: false, bundle_max_positions: "", bundle_max_exposure_pct: "",
  });
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

// 칸(Field)이 없는 오류 — 비중은 종목 행 안의 생 입력, 묶음 한도는 체크박스, 레그 규칙은 펼쳐야 보이는
// 판이라 `data-field` 가 없다. 그래서 "칸이 있는 오류는 칸에, 없는 오류는 바닥에" 로 가른다. 이 목록에
// 없으면 바닥 경고가 건너뛰므로, 비중 합이 틀려 실행이 막혀도 화면에 아무 말이 없게 된다.
export const FIELDLESS_ERROR_FIELDS = Object.freeze(["leg_weights", "use_bundle_risk", "leg_rules"]);

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
  // 묶음: 비중 · 묶음 한도. 종목 수는 중복을 뺀 개수로 센다(buildMacro 가 같은 식으로 쪼갠다).
  const uniqSymbols = [...new Set(symbols)];
  const weightsRaw = splitWeights(form.leg_weights);
  if (weightsRaw.length) {
    if (uniqSymbols.length < 2) return fail("leg_weights", "비중은 종목 2개 이상에서만 정해요.");
    if (weightsRaw.length !== uniqSymbols.length) return fail("leg_weights", `비중을 종목 수(${uniqSymbols.length}개)만큼 적어 주세요.`);
    const nums = weightsRaw.map(Number);
    if (nums.some((n) => !Number.isFinite(n) || n <= 0 || n > 100)) return fail("leg_weights", "비중은 0보다 크고 100 이하인 숫자여야 해요.");
    const total = nums.reduce((a, b) => a + b, 0);
    if (Math.abs(total - 100) > 0.01) return fail("leg_weights", `비중의 합이 ${Number(total.toFixed(2))}% 예요 · 100% 로 맞춰 주세요.`);
  }
  // 묶음이 숏이면 레그 규칙도 숏을 할 수 있어야 한다 — 서버는 레그 규칙의 숏 지원을 보지 않으므로
  // 여기서 막지 않으면 롱만 되는 규칙이 숏 묶음 안에서 조용히 돈다.
  if (isShort) {
    const shortBad = uniqSymbols.find((symbol) => {
      const lrt = form.leg_rules?.[symbol]?.rule_type;
      return lrt && RULE_TYPES[lrt] && !RULE_TYPES[lrt].allowShort;
    });
    if (shortBad) {
      const label = RULE_TYPES[form.leg_rules[shortBad].rule_type].label;
      return fail("leg_rules", `${baseOf(shortBad)} 의 규칙 '${label}' 은 숏을 지원하지 않아요. 묶음이 숏이니 그 종목의 규칙을 바꿔 주세요.`);
    }
  }
  if (form.use_bundle_risk) {
    if (uniqSymbols.length < 2) return fail("use_bundle_risk", "묶음 한도는 종목 2개 이상에서만 쓸 수 있어요.");
    // 한도는 진입 관문을 안전하게 끼울 수 있는 규칙(E~K)에서만 쓸 수 있다. 서버가 거절하므로
    // 여기서 막지 않으면 영어 날 JSON 이 뜬다. 묶음 규칙과 레그마다 덮어쓴 규칙을 둘 다 본다.
    const ruleOf = (symbol) => (form.leg_rules?.[symbol]?.rule_type) || form.rule_type;
    const bad = uniqSymbols.find((s) => !FILTERABLE_RULE_TYPES.includes(ruleOf(s)));
    if (bad) {
      const label = RULE_TYPES[ruleOf(bad)]?.label || ruleOf(bad);
      return fail("use_bundle_risk", `묶음 한도는 '${label}' 규칙에는 쓸 수 없어요. 진입 조건을 달 수 있는 규칙에서만 쓸 수 있어요.`);
    }
    const mp = form.bundle_max_positions, ex = form.bundle_max_exposure_pct;
    const hasMp = mp !== "" && mp != null, hasEx = ex !== "" && ex != null;
    if (hasMp) {
      const n = Number(mp);
      if (!Number.isInteger(n) || n < 1) return fail("bundle_max_positions", "동시 보유 종목 수는 1 이상의 정수예요.");
      if (n >= uniqSymbols.length) return fail("bundle_max_positions", `동시 보유 상한은 종목 수(${uniqSymbols.length}개)보다 작아야 의미가 있어요.`);
    }
    if (hasEx) {
      const n = Number(ex);
      if (!Number.isFinite(n) || n <= 0 || n > 100) return fail("bundle_max_exposure_pct", "총 노출 한도는 0보다 크고 100 이하예요.");
    }
    if (!hasMp && !hasEx) return fail("use_bundle_risk", "묶음 한도를 켰으면 동시 보유 상한이나 총 노출 한도를 하나는 정해 주세요.");
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

// 균등 비중 — 합이 정확히 100 이 되게 마지막 몫이 나머지를 받는다. 서버 검증이 합을
// 0.01 오차로 보므로 떠돌이 소수점을 남기지 않는다.
export function evenWeights(count) {
  const n = Math.max(1, Math.floor(Number(count)) || 1);
  const each = Math.round((100 / n) * 100) / 100;
  const out = Array.from({ length: n - 1 }, () => each);
  out.push(Math.round((100 - each * (n - 1)) * 100) / 100);
  return out;
}

// 쉼표 문자열 → 조각들. 빈 칸은 **자리를 지킨다** — 걸러내면 한 칸을 비우는 사이에 뒤 칸의 숫자가
// 그 자리로 밀려와 다른 종목의 비중이 저절로 바뀐다. 통째로 비었으면(= 손대지 않음) 빈 배열이라
// buildLegs 가 옛 symbols 모양을 그대로 지킨다. 빈 칸만 남은 상태는 '지우는 중' 이고 validateDetailed 가 막는다.
export function splitWeights(raw) {
  const text = String(raw ?? "").trim();
  if (text === "") return [];
  return text.split(",").map((s) => s.trim());
}

// 종목을 더했을 때의 비중 문자열(스펙 §10 "종목을 넣으면 남은 몫을 다시 나눈다").
// 손댄 비중은 뒤엎지 않는다 — 새 종목에 남은 몫(100 − 기존 합)을 주고, 남은 몫이 0 이하면 전체를
// 균등으로 다시 나눈다. 사용자가 정한 숫자를 말없이 바꾸는 것이 합 125% 로 깨진 채 두는 것보다 낫다.
// 손대지 않은 비중("")은 그대로 둔다 — 균등은 종목 수가 바뀌면 저절로 다시 나뉜다.
export function weightsAfterAdd(raw) {
  const list = splitWeights(raw);
  if (!list.length) return "";
  const used = list.reduce((sum, part) => {
    const n = Number(part);
    return sum + (Number.isFinite(n) ? n : 0);
  }, 0);
  const left = Math.round((100 - used) * 100) / 100;
  if (left > 0) return [...list, String(left)].join(", ");
  return evenWeights(list.length + 1).map(String).join(", ");
}

// 중복을 뺀 종목 목록(입력 순서 유지, 최대 5개) — buildMacro 의 symbols 와 같은 규칙.
function symbolList(form) {
  const syms = String(form.symbol || "").split(",").map((s) => s.trim().toUpperCase()).filter(Boolean);
  return [...new Set(syms)].slice(0, 5);
}

// 레그 목록 — 비중도 레그별 규칙도 손대지 않았으면 null. 그러면 buildMacro 가 옛 `symbols`
// 모양을 그대로 내서 기존 매크로가 한 바이트도 달라지지 않는다. 서버가 `legs` 와 `symbols`
// 를 함께 받지 않으므로 "둘 중 하나" 를 여기서 가른다.
export function buildLegs(form) {
  const symbols = symbolList(form);
  if (symbols.length < 2) return null;
  const weights = splitWeights(form.leg_weights);
  // 지금 종목에 해당하고 규칙 타입이 정해진 항목만 센다 — 지운 종목의 찌꺼기는 무시.
  const rules = {};
  for (const symbol of symbols) {
    const rule = form.leg_rules?.[symbol];
    if (rule && rule.rule_type) rules[symbol] = rule;
  }
  // 불러온 매크로가 legs 였으면(leg_shape) 비중을 손대지 않아도 legs 로 되돌린다 — 복제가 모양을 잃지 않게.
  const touched = weights.length > 0 || Object.keys(rules).length > 0 || form.leg_shape === "legs";
  if (!touched) return null;
  const even = evenWeights(symbols.length);
  return symbols.map((symbol, i) => {
    const leg = { symbol, weight: weights[i] !== undefined ? Number(weights[i]) : even[i] };
    const rule = rules[symbol];
    if (rule) {
      // 일부 칸만 채운 규칙도 NaN 이 되지 않게 기본값 위에 얹는다. 자금은 본 매크로의 것을 따른다.
      const full = { ...defaultForm(), initial_capital: form.initial_capital, ...rule };
      leg.rule_type = rule.rule_type;
      // 규칙을 바꾸면 params 를 반드시 함께 보낸다(서버 규칙).
      leg.params = buildParams(rule.rule_type, full);
      const filter = buildEntryFilter(full);
      if (filter) leg.entry_filter = filter;
    }
    return leg;
  });
}

// 묶음 한도 — 체크를 켜고 하나라도 정했을 때만. 아니면 null.
export function buildBundleRisk(form) {
  if (!form.use_bundle_risk) return null;
  const out = {};
  if (form.bundle_max_positions !== "" && form.bundle_max_positions != null) {
    out.max_positions = Number(form.bundle_max_positions);
  }
  if (form.bundle_max_exposure_pct !== "" && form.bundle_max_exposure_pct != null) {
    out.max_exposure_pct = Number(form.bundle_max_exposure_pct);
  }
  return Object.keys(out).length ? out : null;
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
  const legs = buildLegs(form);
  // 한도는 종목이 둘 이상일 때만 의미가 있다.
  const bundleRisk = uniq.length > 1 ? buildBundleRisk(form) : null;
  return {
    exchange: normalizeExchange(form.exchange),
    quote_currency: quoteForExchange(form.exchange),
    symbol: uniq[0] || "BTCUSDT",
    // legs 를 쓰면 symbols 는 null — 서버가 둘을 함께 받지 않는다.
    symbols: !legs && uniq.length > 1 ? uniq.slice(0, 5) : null,
    // legs · bundle_risk 는 있을 때만 키를 둔다 — 기존 매크로 모양(서명)을 바꾸지 않는다.
    ...(legs ? { legs } : {}),
    ...(bundleRisk ? { bundle_risk: bundleRisk } : {}),
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
  if (Array.isArray(macro.legs) && macro.legs.length > 1) {
    f.symbol = macro.legs.map((leg) => leg.symbol).join(", ");
    // legs 였다는 사실을 남긴다 — 균등 묶음을 복제해도 symbols 로 모양이 바뀌지 않게.
    f.leg_shape = "legs";
    const even = evenWeights(macro.legs.length);
    // 균등 판정은 카드·서버 요약과 같은 자리(isEvenWeights)를 쓴다. 다만 균등이라도 숫자가
    // evenWeights 와 다르면(49.99/50.01) 그 값을 그대로 지킨다 — 복제가 비중을 반올림해 버리지 않게.
    const exact = macro.legs.every((leg, i) => Number(leg.weight) === even[i]);
    // 균등이면 비중 입력을 켜지 않는다 — 사용자가 손대지 않은 것을 손댄 것처럼 보이지 않게.
    f.leg_weights = isEvenWeights(macro.legs) && exact ? "" : macro.legs.map((leg) => String(leg.weight)).join(", ");
    f.leg_rules = {};
    for (const leg of macro.legs) {
      if (!leg.rule_type) continue;
      // legs · symbols 를 지워 넘겨야 재귀가 한 번에 끝난다.
      f.leg_rules[leg.symbol] = macroToForm({
        ...macro, rule_type: leg.rule_type, params: leg.params || {},
        entry_filter: leg.entry_filter || null, legs: null, symbols: null, bundle_risk: null,
      });
    }
  }
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
  if (macro.bundle_risk) {
    f.use_bundle_risk = true;
    f.bundle_max_positions = macro.bundle_risk.max_positions ?? "";
    f.bundle_max_exposure_pct = macro.bundle_risk.max_exposure_pct ?? "";
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
