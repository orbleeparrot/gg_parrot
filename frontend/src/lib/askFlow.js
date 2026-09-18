// 껄무새에게 물어볼까? — 카드 상태 머신(순수 리듀서). UI 는 이 상태만 그린다.
// 규칙: 안정형은 선물을 못 고른다 · 종목 최대 3개 · 뒤로 가면 그 뒤 답은 지운다 · 자유 입력 없음.
import { INTERVALS, LEVERAGES, MARKETS, PERIODS, PROFILES } from "./askCopy.js";

export const STEPS = ["profile", "market", "symbols", "period", "interval"];
export const MAX_SYMBOLS = 3;
const PROFILE_ORDER = ["stable", "balanced", "aggressive"];

function emptyAnswers() {
  return { profile: null, market: null, leverage: 1, symbols: [], period: null, interval: null };
}

export function initialState() {
  return { step: "profile", answers: emptyAnswers(), phase: "cards", results: null, remaining: null, error: "" };
}

export function canChooseFutures(answers) {
  return answers.profile !== "stable";
}

function answered(step, answers) {
  if (step === "symbols") return answers.symbols.length > 0 && answers.symbolsConfirmed === true;
  return answers[step] != null;
}

// 아직 답하지 않은 첫 카드. 전부 답했으면 null.
export function nextStep(answers) {
  return STEPS.find((step) => !answered(step, answers)) ?? null;
}

function settle(state, answers) {
  const step = nextStep(answers);
  if (step === null) return { ...state, answers, step: "interval", phase: "ready", error: "" };
  return { ...state, answers, step, phase: "cards", error: "" };
}

// symbolsConfirmed 는 종목을 확정했을 때만 키가 있다(뒤로 가기 테스트가 answers 모양을 정확히 비교한다).
function clearFrom(answers, step) {
  const next = { ...answers };
  for (const s of STEPS.slice(STEPS.indexOf(step))) {
    if (s === "symbols") { next.symbols = []; delete next.symbolsConfirmed; }
    else if (s === "market") { next.market = null; next.leverage = 1; }
    else next[s] = null;
  }
  return next;
}

export function reduce(state, action) {
  const { answers } = state;
  switch (action.type) {
    case "choose": {
      const { step, value } = action;
      if (step === "profile") {
        if (!PROFILES.some((p) => p.value === value)) return state;
        return settle(state, { ...clearFrom(answers, "profile"), profile: value });
      }
      if (step === "market") {
        const market = value?.market;
        const leverage = market === "futures" ? Number(value?.leverage || 1) : 1;
        if (!MARKETS.some((m) => m.value === market)) return state;
        if (market === "futures" && !canChooseFutures(answers)) return state;
        if (!LEVERAGES.includes(leverage)) return state;
        return settle(state, { ...clearFrom(answers, "market"), market, leverage });
      }
      if (step === "period") {
        if (!PERIODS.some((p) => p.value === value)) return state;
        return settle(state, { ...clearFrom(answers, "period"), period: value });
      }
      if (step === "interval") {
        if (!INTERVALS.some((i) => i.value === value)) return state;
        return settle(state, { ...answers, interval: value });
      }
      return state;
    }
    case "toggleSymbol": {
      const symbol = String(action.symbol || "").trim().toUpperCase();
      if (!symbol) return state;
      const has = answers.symbols.includes(symbol);
      if (!has && answers.symbols.length >= MAX_SYMBOLS) return state;
      const symbols = has ? answers.symbols.filter((s) => s !== symbol) : [...answers.symbols, symbol];
      const next = { ...answers, symbols };
      delete next.symbolsConfirmed;
      return { ...state, answers: next, step: "symbols", phase: "cards", error: "" };
    }
    case "confirmSymbols": {
      if (answers.symbols.length === 0) return state;
      return settle(state, { ...answers, symbolsConfirmed: true });
    }
    case "back": {
      if (!STEPS.includes(action.step)) return state;
      return { ...state, answers: clearFrom(answers, action.step), step: action.step, phase: "cards", results: null, error: "" };
    }
    case "loading":
      return { ...state, phase: "loading", error: "" };
    case "results":
      return { ...state, phase: "results", results: action.results || [], remaining: action.remaining ?? state.remaining, error: "" };
    case "error":
      return { ...state, phase: "error", error: String(action.message || "잠시 뒤 다시 물어봐 주세요.") };
    case "followUp": {
      const { kind } = action;
      if (kind === "restart") return initialState();
      if (kind === "symbols") {
        const next = { ...answers, symbols: [] };
        delete next.symbolsConfirmed;
        return { ...state, answers: next, step: "symbols", phase: "cards", results: null, error: "" };
      }
      if (kind === "safer" || kind === "riskier") {
        const idx = PROFILE_ORDER.indexOf(answers.profile);
        const nextIdx = kind === "safer" ? idx - 1 : idx + 1;
        if (nextIdx < 0 || nextIdx >= PROFILE_ORDER.length) return state;
        const profile = PROFILE_ORDER[nextIdx];
        const next = { ...answers, profile };
        if (profile === "stable" && next.market === "futures") { next.market = "spot"; next.leverage = 1; }
        return { ...settle(state, next), results: null };
      }
      return state;
    }
    default:
      return state;
  }
}

export function toRequest(answers) {
  return {
    risk_profile: answers.profile,
    market: answers.market,
    leverage: answers.market === "futures" ? answers.leverage : 1,
    symbols: answers.symbols,
    period_preset: answers.period,
    interval: answers.interval,
  };
}

// 내 말풍선에 쓰는 답 라벨.
export function answerLabel(step, answers) {
  if (step === "profile") return PROFILES.find((p) => p.value === answers.profile)?.label ?? "";
  if (step === "market") {
    if (answers.market === "futures") return `선물 ${answers.leverage}x`;
    return MARKETS.find((m) => m.value === answers.market)?.label ?? "";
  }
  if (step === "symbols") return answers.symbols.join(", ");
  if (step === "period") return PERIODS.find((p) => p.value === answers.period)?.label ?? "";
  if (step === "interval") {
    const found = INTERVALS.find((i) => i.value === answers.interval);
    return found ? `${found.label} (${found.hint})` : "";
  }
  return "";
}
