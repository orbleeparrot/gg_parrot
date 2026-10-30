// 껄무새에게 물어볼까? v2 — 카드 상태 머신(순수 리듀서). UI 는 이 상태만 그린다.
// 규칙: 안정형은 선물을 못 고른다 · 종목은 후보/직접 고르기 목록에서 하나만 · 뒤로 가면 세션을 버린다 · 자유 입력 없음.
import { HORIZONS, LEVERAGES, MARKETS, PROFILES, WATCH_LEVELS } from "./askCopy.js";
import { EXCHANGES, exchangeLabel, isDomestic, quoteForExchange } from "./exchanges.js";

// 거래소 바로 다음에 갈림길을 둔다 — 종목 하나로 볼지, 여러 종목을 묶어서 볼지.
// 묶음 쪽은 마지막에 종목 하나가 아니라 **여럿**을 고르고, 서버가 묶음 매크로만 만든다.
export const STEPS = ["exchange", "mode", "balance", "profile", "market", "horizon", "watch"];
export const MODES = [
  { value: "single", label: "종목 하나", hint: "한 종목에 집중해서 볼래요" },
  { value: "bundle", label: "여러 종목 묶음", hint: "자금을 나눠 담아 볼래요" },
];
export const isBundle = (answers) => answers?.mode === "bundle";
export const PROFILE_ORDER = ["stable", "balanced", "aggressive"];
// 바이낸스 USDT 짝 모양 — 서버의 _SYMBOL_RE 와 같은 규칙.
const SYMBOL_RE = /^[A-Z0-9]{1,20}USDT$/;
// 한 묶음의 종목 수 상한 — 서버 engine/schema.py 의 Macro.MAX_SYMBOLS 와 같아야 한다.
export const MAX_BUNDLE_SYMBOLS = 10;

export function canChooseFutures(answers) {
  return !isDomestic(answers.exchange) && answers.profile !== "stable";
}

// 금액 입력칸 — 세 자리마다 쉼표. KRW 는 원 단위 정수(1,000,000), USDT 는 미국 달러처럼
// 소수 둘째 자리까지(1,234.56). 숫자·소수점 하나만 남긴다.
export function formatAmountInput(raw, quote = "USDT") {
  const decimals = quote === "KRW" ? 0 : 2;
  const clean = String(raw ?? "").replace(/[^0-9.]/g, "");
  const dot = decimals ? clean.indexOf(".") : -1;
  const intPart = (dot < 0 ? clean.replace(/\./g, "") : clean.slice(0, dot)).replace(/^0+(?=\d)/, "");
  const grouped = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return dot < 0 ? grouped : `${grouped || "0"}.${clean.slice(dot + 1).replace(/\./g, "").slice(0, decimals)}`;
}
export const parseAmountInput = (text) => String(text ?? "").replace(/,/g, "");

export function validBalance(value) {
  return typeof value !== "boolean" && value != null && String(value).trim() !== "" && Number.isFinite(Number(value)) && Number(value) > 0;
}

function emptyAnswers() {
  return { exchange: null, mode: null, balance: null, profile: null, market: null, leverage: 1, horizon: null, watch: null, symbol: null, symbols: [] };
}

export function initialState() {
  return {
    step: "exchange", answers: emptyAnswers(), phase: "cards",
    session: null, candidates: [], manualSymbols: [],
    results: null, noEdge: false, noEdgeBest: null, refunded: false, remaining: null, error: "",
  };
}

function answered(step, answers) {
  return answers[step] != null;
}

export function nextStep(answers) {
  return STEPS.find((step) => !answered(step, answers)) ?? null;
}

function settle(state, answers) {
  const step = nextStep(answers);
  if (step === null) return { ...state, answers, step: "watch", phase: "ready", error: "" };
  return { ...state, answers, step, phase: "cards", error: "" };
}

// 카드 하나를 고치면 그 뒤 답과 세션·후보·결과를 전부 버린다.
function clearFrom(answers, step) {
  const next = { ...answers, symbol: null, symbols: [] };
  for (const s of STEPS.slice(STEPS.indexOf(step))) {
    if (s === "market") { next.market = null; next.leverage = 1; }
    else next[s] = null;
  }
  return next;
}

function dropFlow(state) {
  return { ...state, session: null, candidates: [], manualSymbols: [], results: null };
}

function allowedSymbols(state) {
  return new Set([...state.candidates.map((c) => c.symbol), ...state.manualSymbols]);
}

export function reduce(state, action) {
  const { answers } = state;
  switch (action.type) {
    case "choose": {
      const { step, value } = action;
      if (step === "exchange") {
        if (!EXCHANGES.some((item) => item.value === value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "exchange"), exchange: value });
      }
      if (step === "mode") {
        if (!MODES.some((item) => item.value === value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "mode"), mode: value });
      }
      if (step === "balance") {
        if (!answers.exchange || !validBalance(value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "balance"), balance: Number(value) });
      }
      if (step === "profile") {
        if (!PROFILES.some((p) => p.value === value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "profile"), profile: value });
      }
      if (step === "market") {
        const market = value?.market;
        const leverage = market === "futures" ? Number(value?.leverage || 1) : 1;
        if (!MARKETS.some((m) => m.value === market)) return state;
        if (market === "futures" && !canChooseFutures(answers)) return state;
        if (!LEVERAGES.includes(leverage)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "market"), market, leverage });
      }
      if (step === "horizon") {
        if (!HORIZONS.some((h) => h.value === value)) return state;
        return settle(dropFlow(state), { ...clearFrom(answers, "horizon"), horizon: value });
      }
      if (step === "watch") {
        if (!WATCH_LEVELS.some((w) => w.value === value)) return state;
        return settle(dropFlow(state), { ...answers, watch: value, symbol: null, symbols: [] });
      }
      return state;
    }
    case "loadingCandidates":
      return { ...state, phase: "loadingCandidates", error: "" };
    case "candidates":
      return {
        ...state, phase: "candidates", error: "",
        session: action.session || null,
        candidates: action.candidates || [],
        manualSymbols: action.manualSymbols || [],
        remaining: action.session?.remaining ?? state.remaining,
        results: null,
      };
    case "chooseSymbol": {
      const symbol = String(action.symbol || "").trim().toUpperCase();
      if (!symbol) return state;
      // resolved 는 '직접 고를래요' 검색이 거래 가능 목록에서 실제로 찾아낸 종목이라는 뜻.
      // 그 경우에도 짝 모양은 확인하고, 최종 판정은 서버가 다시 한다.
      const shape = isDomestic(answers.exchange) ? /^KRW-[A-Z0-9]{1,20}$/.test(symbol) : SYMBOL_RE.test(symbol);
      const ok = shape && (action.resolved || allowedSymbols(state).has(symbol));
      if (!ok) return state;
      return { ...state, answers: { ...answers, symbol }, error: "" };
    }
    case "toggleSymbol": {
      // 묶음 전용 — 누르면 넣고, 다시 누르면 뺀다. 상한은 서버 engine 과 같은 값이다.
      if (!isBundle(answers)) return state;
      const symbol = String(action.symbol || "").trim().toUpperCase();
      if (!symbol) return state;
      const shape = isDomestic(answers.exchange) ? /^KRW-[A-Z0-9]{1,20}$/.test(symbol) : SYMBOL_RE.test(symbol);
      if (!shape) return state;
      const picked = answers.symbols || [];
      if (picked.includes(symbol)) {
        return { ...state, answers: { ...answers, symbols: picked.filter((item) => item !== symbol) }, error: "" };
      }
      if (!(action.resolved || allowedSymbols(state).has(symbol))) return state;
      if (picked.length >= MAX_BUNDLE_SYMBOLS) {
        return { ...state, error: `묶음은 종목 ${MAX_BUNDLE_SYMBOLS}개까지예요.` };
      }
      return { ...state, answers: { ...answers, symbols: [...picked, symbol] }, error: "" };
    }
    case "setBundle": {
      // 추천 묶음 하나를 통째로 — 고르던 것은 지우고 바꾼다.
      if (!isBundle(answers)) return state;
      const list = (action.symbols || []).map((item) => String(item || "").trim().toUpperCase()).filter(Boolean);
      const uniq = [...new Set(list)].slice(0, MAX_BUNDLE_SYMBOLS);
      if (uniq.length < 2) return state;
      return { ...state, answers: { ...answers, symbols: uniq }, error: "" };
    }
    case "loadingResults":
      return { ...state, phase: "loadingResults", error: "" };
    case "results":
      return { ...state, phase: "results", results: action.results || [],
               noEdge: !!action.noEdge, noEdgeBest: action.noEdgeBest || null,
               refunded: !!action.refunded,
               remaining: action.remaining ?? state.remaining, error: "" };
    case "error":
      return { ...state, phase: "error", error: String(action.message || "잠시 뒤 다시 물어봐 주세요.") };
    case "back": {
      if (!STEPS.includes(action.step)) return state;
      return { ...dropFlow(state), answers: clearFrom(answers, action.step),
               step: action.step, phase: "cards", error: "" };
    }
    case "followUp": {
      const { kind } = action;
      if (kind === "restart") return initialState();
      if (state.phase !== "results") return state;
      if (kind === "symbols") {
        // 세션을 그대로 두고 후보 목록으로 — 차감 없음.
        return { ...state, phase: "candidates", results: null,
                 answers: { ...answers, symbol: null }, error: "" };
      }
      if (kind === "safer" || kind === "riskier") {
        const idx = PROFILE_ORDER.indexOf(answers.profile);
        const nextIdx = kind === "safer" ? idx - 1 : idx + 1;
        if (nextIdx < 0 || nextIdx >= PROFILE_ORDER.length) return state;
        const profile = PROFILE_ORDER[nextIdx];
        const next = { ...answers, profile, symbol: null };
        if (profile === "stable" && next.market === "futures") { next.market = "spot"; next.leverage = 1; }
        // 성향이 바뀌면 후보가 달라진다 — 세션을 버리고 다시 제출하게 한다(새로 1회 차감).
        return settle(dropFlow(state), next);
      }
      return state;
    }
    default:
      return state;
  }
}

export function toCandidatesRequest(answers) {
  return {
    exchange: answers.exchange,
    account_balance: answers.balance,
    risk_profile: answers.profile,
    market: answers.market,
    leverage: answers.market === "futures" ? answers.leverage : 1,
    invest_horizon: answers.horizon,
    watch_frequency: answers.watch,
  };
}

export function toAskRequest(state) {
  const { answers } = state;
  if (isBundle(answers)) {
    const picked = answers.symbols || [];
    // symbol 은 대표 한 종목 — 서버가 옛 모양도 받으므로 둘 다 보낸다.
    return { session_id: state.session?.id, symbol: picked[0], symbols: picked };
  }
  return { session_id: state.session?.id, symbol: answers.symbol };
}

// 물어볼 준비가 됐는가 — 묶음은 둘 이상이어야 묶음이다.
export function readyToAsk(state) {
  const { answers } = state;
  return isBundle(answers) ? (answers.symbols || []).length >= 2 : !!answers.symbol;
}

// 내 말풍선에 쓰는 답 라벨.
export function answerLabel(step, answers) {
  if (step === "exchange") return answers.exchange ? exchangeLabel(answers.exchange) : "";
  if (step === "mode") return MODES.find((item) => item.value === answers.mode)?.label ?? "";
  if (step === "balance") return `${Number(answers.balance).toLocaleString("ko-KR", { maximumFractionDigits: 8 })} ${quoteForExchange(answers.exchange)}`;
  if (step === "profile") return PROFILES.find((p) => p.value === answers.profile)?.label ?? "";
  if (step === "market") {
    if (answers.market === "futures") return `선물 ${answers.leverage}x`;
    return MARKETS.find((m) => m.value === answers.market)?.label ?? "";
  }
  if (step === "horizon") return HORIZONS.find((h) => h.value === answers.horizon)?.label ?? "";
  if (step === "watch") return WATCH_LEVELS.find((w) => w.value === answers.watch)?.label ?? "";
  return "";
}

export { extraOffer } from "./askExtra.js";
