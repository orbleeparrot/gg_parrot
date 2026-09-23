// 껄무새에게 물어볼까? v2 — 카드 상태 머신(순수 리듀서). UI 는 이 상태만 그린다.
// 규칙: 안정형은 선물을 못 고른다 · 종목은 후보/직접 고르기 목록에서 하나만 · 뒤로 가면 세션을 버린다 · 자유 입력 없음.
import { HORIZONS, LEVERAGES, MARKETS, PROFILES, WATCH_LEVELS } from "./askCopy.js";

export const STEPS = ["profile", "market", "horizon", "watch"];
export const PROFILE_ORDER = ["stable", "balanced", "aggressive", "scalper"];

export function canChooseFutures(answers) {
  return answers.profile !== "stable";
}

function emptyAnswers() {
  return { profile: null, market: null, leverage: 1, horizon: null, watch: null, symbol: null };
}

export function initialState() {
  return {
    step: "profile", answers: emptyAnswers(), phase: "cards",
    session: null, candidates: [], manualSymbols: [],
    results: null, remaining: null, error: "",
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
  const next = { ...answers, symbol: null };
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
        return settle(dropFlow(state), { ...answers, watch: value, symbol: null });
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
      if (!symbol || !allowedSymbols(state).has(symbol)) return state;
      return { ...state, answers: { ...answers, symbol }, error: "" };
    }
    case "loadingResults":
      return { ...state, phase: "loadingResults", error: "" };
    case "results":
      return { ...state, phase: "results", results: action.results || [],
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
    risk_profile: answers.profile,
    market: answers.market,
    leverage: answers.market === "futures" ? answers.leverage : 1,
    invest_horizon: answers.horizon,
    watch_frequency: answers.watch,
  };
}

export function toAskRequest(state) {
  return { session_id: state.session?.id, symbol: state.answers.symbol };
}

// 내 말풍선에 쓰는 답 라벨.
export function answerLabel(step, answers) {
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
