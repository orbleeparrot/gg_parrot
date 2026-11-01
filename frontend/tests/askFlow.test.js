import assert from "node:assert/strict";
import test from "node:test";
import { MAX_BUNDLE_SYMBOLS, STEPS, initialState, isBundle, readyToAsk, reduce, toCandidatesRequest, toAskRequest, canChooseFutures } from "../src/lib/askFlow.js";

const CANDS = [
  { symbol: "AAAUSDT", base: "AAA", reason: "거래가 활발해요", volume_rank: 1, range_pct: 1, change_pct: 1 },
  { symbol: "BBBUSDT", base: "BBB", reason: "변동이 적당해요", volume_rank: 2, range_pct: 5, change_pct: 1 },
  { symbol: "CCCUSDT", base: "CCC", reason: "변동이 커요", volume_rank: 3, range_pct: 20, change_pct: 1 },
];

function cards(profile = "balanced", mode = "single") {
  let s = initialState();
  s = reduce(s, { type: "choose", step: "exchange", value: "binance" });
  s = reduce(s, { type: "choose", step: "mode", value: mode });
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
  assert.deepEqual(STEPS, ["exchange", "mode", "balance", "profile", "market", "horizon", "watch"]);
  const s = cards();
  assert.equal(s.phase, "ready");
  assert.deepEqual(toCandidatesRequest(s.answers), {
    exchange: "binance", account_balance: 250,
    risk_profile: "balanced", market: "spot", leverage: 1,
    invest_horizon: "weeks", watch_frequency: "sometimes",
  });
});

test("manual balance must be positive and finite before progressing", () => {
  let s = reduce(initialState(), { type: "choose", step: "exchange", value: "upbit" });
  assert.equal(s.step, "mode", "거래소 다음은 갈림길이다");
  s = reduce(s, { type: "choose", step: "mode", value: "single" });
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
  s = reduce(s, { type: "choose", step: "mode", value: "single" });
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
  const domestic = reduce(reduce(initialState(), { type: "choose", step: "exchange", value: "upbit" }),
                          { type: "choose", step: "mode", value: "single" });
  assert.equal(reduce(domestic, { type: "chooseSymbol", symbol: "KRW-T", resolved: true }).answers.symbol, "KRW-T");
});

test("금액 입력칸은 세 자리마다 쉼표 — KRW 는 정수, USDT 는 소수 둘째 자리까지", async () => {
  const { formatAmountInput, parseAmountInput, validBalance } = await import("../src/lib/askFlow.js");
  assert.equal(formatAmountInput("1234567", "KRW"), "1,234,567");
  assert.equal(formatAmountInput("1,2345", "KRW"), "12,345");
  assert.equal(formatAmountInput("0001000", "KRW"), "1,000");
  assert.equal(formatAmountInput("1234.56", "KRW"), "123,456");
  assert.equal(formatAmountInput("1234.5678", "USDT"), "1,234.56");
  assert.equal(formatAmountInput("1234.", "USDT"), "1,234.");
  assert.equal(formatAmountInput("12.3.4", "USDT"), "12.34");
  assert.equal(formatAmountInput(".5", "USDT"), "0.5");
  assert.equal(formatAmountInput("abc", "USDT"), "");
  assert.equal(parseAmountInput("1,234,567"), "1234567");
  assert.ok(validBalance(parseAmountInput("100,000")));
  assert.ok(!validBalance(parseAmountInput("0")));
});


// --- 묶음으로 물어보기 (2026-10-30) ------------------------------------------
//
// 거래소 다음의 갈림길. 묶음 쪽은 종목을 **여럿** 고르고 한 번에 물어본다.

function bundleCards() {
  return reduce(cards("balanced", "bundle"), {
    type: "candidates",
    session: { id: 7, remaining: 4 },
    candidates: CANDS,
    manualSymbols: ["BTCUSDT"],
  });
}

test("묶음 갈림길을 고르면 종목을 여럿 담는다", () => {
  let s = bundleCards();
  assert.equal(isBundle(s.answers), true);
  s = reduce(s, { type: "toggleSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "toggleSymbol", symbol: "BBBUSDT" });
  assert.deepEqual(s.answers.symbols, ["AAAUSDT", "BBBUSDT"]);
  assert.deepEqual(toAskRequest(s), { session_id: 7, symbol: "AAAUSDT", symbols: ["AAAUSDT", "BBBUSDT"] });
});

test("같은 종목을 다시 누르면 빠진다", () => {
  let s = reduce(bundleCards(), { type: "toggleSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "toggleSymbol", symbol: "AAAUSDT" });
  assert.deepEqual(s.answers.symbols, []);
});

test("한 종목만으로는 묶음이 아니다 — 물어볼 수 없다", () => {
  const s = reduce(bundleCards(), { type: "toggleSymbol", symbol: "AAAUSDT" });
  assert.equal(readyToAsk(s), false);
  assert.equal(readyToAsk(reduce(s, { type: "toggleSymbol", symbol: "BBBUSDT" })), true);
});

test("고를 수 있는 목록 밖의 종목은 안 담긴다", () => {
  const s = reduce(bundleCards(), { type: "toggleSymbol", symbol: "NOPEUSDT" });
  assert.deepEqual(s.answers.symbols, []);
  // 검색으로 실제 목록에서 찾아낸 것(resolved)은 담긴다.
  assert.deepEqual(reduce(s, { type: "toggleSymbol", symbol: "NOPEUSDT", resolved: true }).answers.symbols, ["NOPEUSDT"]);
});

test("상한을 넘기면 담지 않고 까닭을 말한다", () => {
  let s = bundleCards();
  for (let n = 0; n < MAX_BUNDLE_SYMBOLS; n += 1) {
    s = reduce(s, { type: "toggleSymbol", symbol: `S${n}USDT`, resolved: true });
  }
  assert.equal(s.answers.symbols.length, MAX_BUNDLE_SYMBOLS);
  const over = reduce(s, { type: "toggleSymbol", symbol: "ONEMOREUSDT", resolved: true });
  assert.equal(over.answers.symbols.length, MAX_BUNDLE_SYMBOLS);
  assert.match(over.error, /10개/);
});

test("추천 묶음은 고르던 것을 지우고 바꾼다", () => {
  let s = reduce(bundleCards(), { type: "toggleSymbol", symbol: "AAAUSDT" });
  s = reduce(s, { type: "setBundle", symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"] });
  assert.deepEqual(s.answers.symbols, ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "섞이면 묶음 크기가 이름과 어긋난다");
});

test("종목 하나 쪽에서는 묶음 동작이 아무 일도 안 한다", () => {
  const single = withCandidates();
  assert.deepEqual(reduce(single, { type: "toggleSymbol", symbol: "AAAUSDT" }).answers.symbols, []);
  assert.deepEqual(reduce(single, { type: "setBundle", symbols: ["AAAUSDT", "BBBUSDT"] }).answers.symbols, []);
  assert.deepEqual(toAskRequest(reduce(single, { type: "chooseSymbol", symbol: "AAAUSDT" })),
                   { session_id: 7, symbol: "AAAUSDT" });
});

test("갈림길을 바꾸면 담아 둔 종목이 사라진다", () => {
  const s = reduce(bundleCards(), { type: "toggleSymbol", symbol: "AAAUSDT" });
  const back = reduce(s, { type: "choose", step: "mode", value: "single" });
  assert.deepEqual(back.answers.symbols, []);
  assert.equal(back.session, null, "답을 바꾸면 세션도 버린다");
});
