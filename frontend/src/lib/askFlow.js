// 껄무새에게 물어볼까? — 카드 상태 머신(순수 리듀서). UI 는 이 상태만 그린다.
// 규칙: 안정형은 선물을 못 고른다 · 종목 최대 3개 · 뒤로 가면 그 뒤 답은 지운다 · 자유 입력 없음.
import { INTERVALS, LEVERAGES, MARKETS, ONE_MINUTE_NEEDS_WEEK, PERIODS, PROFILES, SHORT_INTERVALS, SHORT_PERIODS } from "./askCopy.js";

export const STEPS = ["profile", "market", "symbols", "period", "interval"];
export const PROFILE_ORDER = ["stable", "balanced", "aggressive", "scalper"];
export const MAX_SYMBOLS = 3;  // 긴 성향 기본값 — 실제 상한은 maxSymbols(profile)

export function isShort(profile) { return profile === "scalper"; }
export function maxSymbols(profile) { return isShort(profile) ? 2 : MAX_SYMBOLS; }
export function periodOptions(profile) { return isShort(profile) ? SHORT_PERIODS : PERIODS; }
// 1분 봉은 최근 1주까지만 — 기간이 1개월이면 1분 칩을 비활성으로 돌려준다.
export function intervalOptions(profile, period) {
  if (!isShort(profile)) return INTERVALS;
  return SHORT_INTERVALS.map((o) => (o.value === "1m" && period === "1m" ? { ...o, disabled: true, title: ONE_MINUTE_NEEDS_WEEK } : o));
}

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
        if (!periodOptions(answers.profile).some((p) => p.value === value)) return state;
        return settle(state, { ...clearFrom(answers, "period"), period: value });
      }
      if (step === "interval") {
        const opt = intervalOptions(answers.profile, answers.period).find((i) => i.value === value);
        if (!opt || opt.disabled) return state;
        return settle(state, { ...answers, interval: value });
      }
      return state;
    }
    case "toggleSymbol": {
      const symbol = String(action.symbol || "").trim().toUpperCase();
      if (!symbol) return state;
      const has = answers.symbols.includes(symbol);
      if (!has && answers.symbols.length >= maxSymbols(answers.profile)) return state;
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
      // restart 은 대화에서 닫을 때도 쓰고 에러 상태에서도 쓰므로 모든 phase에서 허락
      if (kind === "restart") return initialState();
      // 다른 follow-up 은 결과를 보여주는 phase 에서만 작동
      if (state.phase !== "results") return state;
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
        // 단타형 ↔ 그 외는 기간·봉 선택지가 달라 답을 지운다. 종목이 새 상한을 넘으면 종목도 지운다.
        if (isShort(profile) !== isShort(answers.profile)) { next.period = null; next.interval = null; }
        if (next.symbols.length > maxSymbols(profile)) { next.symbols = []; delete next.symbolsConfirmed; }
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
  if (step === "period") return [...PERIODS, ...SHORT_PERIODS].find((p) => p.value === answers.period)?.label ?? "";
  if (step === "interval") {
    const found = [...INTERVALS, ...SHORT_INTERVALS].find((i) => i.value === answers.interval);
    return found ? `${found.label} (${found.hint})` : "";
  }
  return "";
}
