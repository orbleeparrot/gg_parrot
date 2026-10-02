import assert from "node:assert/strict";
import test from "node:test";
import { STEPS, initialState, reduce, toCandidatesRequest, toAskRequest, canChooseFutures } from "../src/lib/askFlow.js";

const CANDS = [
  { symbol: "AAAUSDT", base: "AAA", reason: "거래가 활발해요", volume_rank: 1, range_pct: 1, change_pct: 1 },
  { symbol: "BBBUSDT", base: "BBB", reason: "변동이 적당해요", volume_rank: 2, range_pct: 5, change_pct: 1 },
  { symbol: "CCCUSDT", base: "CCC", reason: "변동이 커요", volume_rank: 3, range_pct: 20, change_pct: 1 },
];

function cards(profile = "balanced") {
  let s = initialState();
  s = reduce(s, { type: "choose", step: "exchange", value: "binance" });
  s = reduce(s, { type: "choose", step: "balance", value: 250 });
  s = reduce(s, { type: "choose", step: "profile", value: profile });
  s = reduce(s, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  s = reduce(s, { type: "choose", step: "horizon", value: "weeks" });
  s = reduce(s, { type: "choose", step: "watch", value: "sometimes" });
  return s;
}

function withCandidates() {
  return reduce(cards(), {
    type: "candidates",
    session: { id: 7, remaining: 4 },
    candidates: CANDS,
    manualSymbols: ["BTCUSDT"],
  });
}

test("exchange and manual balance precede profile cards and end ready", () => {
  assert.equal(initialState().step, "exchange");
  assert.deepEqual(STEPS, ["exchange", "balance", "profile", "market", "horizon", "watch"]);
  const s = cards();
  assert.equal(s.phase, "ready");
  assert.deepEqual(toCandidatesRequest(s.answers), {
    exchange: "binance", account_balance: 250,
    risk_profile: "balanced", market: "spot", leverage: 1,
    invest_horizon: "weeks", watch_frequency: "sometimes",
  });
});

test("manual balance must be positive and finite before progressing", () => {
  const s = reduce(initialState(), { type: "choose", step: "exchange", value: "upbit" });
  assert.equal(s.step, "balance");
  for (const value of [0, -1, "", "Infinity", NaN, true]) {
    assert.equal(reduce(s, { type: "choose", step: "balance", value }).answers.balance, null);
  }
  const next = reduce(s, { type: "choose", step: "balance", value: "150000" });
  assert.equal(next.answers.balance, 150000);
  assert.equal(next.step, "profile");
});

test("domestic exchange cannot choose futures or a USDT pair", () => {
  let s = reduce(initialState(), { type: "choose", step: "exchange", value: "bithumb" });
  s = reduce(s, { type: "choose", step: "balance", value: 100000 });
  s = reduce(s, { type: "choose", step: "profile", value: "aggressive" });
  assert.equal(canChooseFutures(s.answers), false);
  assert.equal(reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } }).answers.market, null);
  assert.equal(reduce(s, { type: "chooseSymbol", symbol: "BTCUSDT", resolved: true }).answers.symbol, null);
  assert.equal(reduce(s, { type: "chooseSymbol", symbol: "KRW-BTC", resolved: true }).answers.symbol, "KRW-BTC");
});

test("changing exchange clears balance and the server candidate session", () => {
  const s = reduce(withCandidates(), { type: "choose", step: "exchange", value: "upbit" });
  assert.equal(s.answers.balance, null);
  assert.equal(s.answers.profile, null);
  assert.equal(s.session, null);
  assert.deepEqual(s.candidates, []);
});

test("stable profile cannot choose futures", () => {
  let s = reduce(initialState(), { type: "choose", step: "profile", value: "stable" });
  assert.equal(canChooseFutures(s.answers), false);
  s = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } });
  assert.equal(s.answers.market, null);
});

test("candidates phase holds the session and the list", () => {
  const s = withCandidates();
  assert.equal(s.phase, "candidates");
  assert.equal(s.session.id, 7);
  assert.equal(s.candidates.length, 3);
  assert.deepEqual(s.manualSymbols, ["BTCUSDT"]);
});

test("only one symbol can be chosen", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "aaausdt" });
  assert.equal(s.answers.symbol, "AAAUSDT");
  s = reduce(s, { type: "chooseSymbol", symbol: "BBBUSDT" });
  assert.equal(s.answers.symbol, "BBBUSDT");
  assert.deepEqual(toAskRequest(s), { session_id: 7, symbol: "BBBUSDT" });
});

test("a symbol outside the candidates and manual list is ignored", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "SCAMUSDT" });
  assert.equal(s.answers.symbol, null);
});

test("a manual symbol is accepted", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "BTCUSDT" });
  assert.equal(s.answers.symbol, "BTCUSDT");
});

test("going back to a card drops the session, candidates and results", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 3 });
  s = reduce(s, { type: "back", step: "horizon" });
  assert.equal(s.phase, "cards");
  assert.equal(s.session, null);
  assert.deepEqual(s.candidates, []);
  assert.equal(s.results, null);
  assert.equal(s.answers.symbol, null);
  assert.equal(s.answers.horizon, null);
});

test("'다른 종목으로' returns to the candidates and keeps the session", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 3 });
  s = reduce(s, { type: "followUp", kind: "symbols" });
  assert.equal(s.phase, "candidates");
  assert.equal(s.session.id, 7);
  assert.equal(s.candidates.length, 3);
  assert.equal(s.answers.symbol, null);
});

test("'안전하게' changes the profile and returns to the cards (a new use)", () => {
  let s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 3 });
  s = reduce(s, { type: "followUp", kind: "safer" });
  assert.equal(s.answers.profile, "stable");
  assert.equal(s.phase, "ready");   // 나머지 답은 남아 있어 바로 제출 가능
  assert.equal(s.session, null);    // 세션은 버린다 — 다시 제출하면 새로 차감
  assert.deepEqual(s.candidates, []);
});

test("changing to stable drops a futures answer", () => {
  let s = cards("balanced");
  s = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } });
  s = reduce(s, { type: "choose", step: "horizon", value: "weeks" });
  s = reduce(s, { type: "choose", step: "watch", value: "sometimes" });
  s = reduce(s, { type: "candidates", session: { id: 1, remaining: 1 }, candidates: CANDS, manualSymbols: [] });
  s = reduce(s, { type: "results", results: [], remaining: 1 });
  s = reduce(s, { type: "followUp", kind: "safer" });
  assert.equal(s.answers.profile, "stable");
  assert.equal(s.answers.market, "spot");
  assert.equal(s.answers.leverage, 1);
});

test("restart clears everything", () => {
  const s = reduce(withCandidates(), { type: "followUp", kind: "restart" });
  assert.deepEqual(s, initialState());
});

// 직접 고를래요 — 검색으로 거래 가능 목록에서 고른 종목도 받는다 (2026-09-23)
test("a symbol resolved from the tradable list is accepted", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "mubarakusdt", resolved: true });
  assert.equal(s.answers.symbol, "MUBARAKUSDT");
  assert.deepEqual(toAskRequest(s), { session_id: 7, symbol: "MUBARAKUSDT" });
});

test("an unresolved symbol outside the lists is still ignored", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "MUBARAKUSDT" });
  assert.equal(s.answers.symbol, null);
});

test("a resolved symbol that is not shaped like a pair is ignored", () => {
  const s = reduce(withCandidates(), { type: "chooseSymbol", symbol: "NOT A SYMBOL", resolved: true });
  assert.equal(s.answers.symbol, null);
});

test("a one-letter Binance base is accepted from candidates, manual chips, and resolved search", () => {
  for (const source of ["candidate", "manual", "resolved"]) {
    const state = withCandidates();
    if (source === "candidate") state.candidates = [{ symbol: "TUSDT", base: "T" }];
    if (source === "manual") state.manualSymbols = ["TUSDT"];
    const next = reduce(state, { type: "chooseSymbol", symbol: " tusdt ", resolved: source === "resolved" });
    assert.equal(next.answers.symbol, "TUSDT", source);
    assert.deepEqual(toAskRequest(next), { session_id: 7, symbol: "TUSDT" });
  }
});

test("Binance pair shape requires a 1-to-20 character base and excludes native KRW pairs", () => {
  for (const base of ["T", "0", "A".repeat(20)]) {
    assert.equal(reduce(withCandidates(), { type: "chooseSymbol", symbol: `${base}USDT`, resolved: true }).answers.symbol, `${base}USDT`);
  }
  for (const symbol of ["USDT", "A".repeat(21) + "USDT", "KRW-T", "T-USDT", "TUSDT,BTCUSDT"]) {
    assert.equal(reduce(withCandidates(), { type: "chooseSymbol", symbol, resolved: true }).answers.symbol, null);
  }
  assert.equal(reduce(withCandidates(), { type: "chooseSymbol", symbol: "TUSDT" }).answers.symbol, null);
  const domestic = reduce(initialState(), { type: "choose", step: "exchange", value: "upbit" });
  assert.equal(reduce(domestic, { type: "chooseSymbol", symbol: "KRW-T", resolved: true }).answers.symbol, "KRW-T");
});
