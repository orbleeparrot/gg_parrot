import assert from "node:assert/strict";
import test from "node:test";
import { STEPS, initialState, reduce, toRequest, canChooseFutures, answerLabel } from "../src/lib/askFlow.js";

const walk = (...actions) => actions.reduce(reduce, initialState());

test("cards run in order and the state becomes ready after the last answer", () => {
  assert.deepEqual(STEPS, ["profile", "market", "symbols", "period", "interval"]);
  let s = initialState();
  assert.equal(s.step, "profile");
  s = reduce(s, { type: "choose", step: "profile", value: "balanced" });
  assert.equal(s.step, "market");
  s = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } });
  assert.equal(s.step, "symbols");
  s = reduce(s, { type: "toggleSymbol", symbol: "btcusdt" });
  s = reduce(s, { type: "toggleSymbol", symbol: "ETHUSDT" });
  assert.deepEqual(s.answers.symbols, ["BTCUSDT", "ETHUSDT"]);
  assert.equal(s.step, "symbols");
  s = reduce(s, { type: "confirmSymbols" });
  assert.equal(s.step, "period");
  s = reduce(s, { type: "choose", step: "period", value: "6m" });
  s = reduce(s, { type: "choose", step: "interval", value: "4h" });
  assert.equal(s.phase, "ready");
  assert.deepEqual(toRequest(s.answers), {
    risk_profile: "balanced", market: "futures", leverage: 2, symbols: ["BTCUSDT", "ETHUSDT"], period_preset: "6m", interval: "4h",
  });
  assert.equal(answerLabel("market", s.answers), "선물 2x");
});

test("stable profile cannot choose futures", () => {
  let s = reduce(initialState(), { type: "choose", step: "profile", value: "stable" });
  assert.equal(canChooseFutures(s.answers), false);
  const rejected = reduce(s, { type: "choose", step: "market", value: { market: "futures", leverage: 1 } });
  assert.equal(rejected, s);
  s = reduce(s, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  assert.equal(s.step, "symbols");
});

test("symbols: max three, dedupe, confirm needs at least one", () => {
  let s = walk({ type: "choose", step: "profile", value: "aggressive" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  const empty = reduce(s, { type: "confirmSymbols" });
  assert.equal(empty, s);
  for (const sym of ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BTCUSDT"]) s = reduce(s, { type: "toggleSymbol", symbol: sym });
  assert.deepEqual(s.answers.symbols, ["ETHUSDT", "SOLUSDT"]);  // 4번째 거부, 마지막 BTC 토글로 제거
});

test("going back clears later answers; follow-ups jump to the right card", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "balanced" },
    { type: "choose", step: "market", value: { market: "spot", leverage: 1 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "3m" }, { type: "choose", step: "interval", value: "1d" },
  );
  assert.equal(s.phase, "ready");
  const back = reduce(s, { type: "back", step: "market" });
  assert.equal(back.step, "market");
  assert.equal(back.phase, "cards");
  assert.deepEqual(back.answers, { profile: "balanced", market: null, leverage: 1, symbols: [], period: null, interval: null });

  const results = reduce(s, { type: "results", results: [{ label: "x" }], remaining: 4 });
  assert.equal(results.phase, "results");
  const safer = reduce(results, { type: "followUp", kind: "safer" });
  assert.equal(safer.answers.profile, "stable");
  assert.equal(safer.phase, "ready");
  const riskier = reduce(results, { type: "followUp", kind: "riskier" });
  assert.equal(riskier.answers.profile, "aggressive");
  const other = reduce(results, { type: "followUp", kind: "symbols" });
  assert.equal(other.step, "symbols");
  assert.deepEqual(other.answers.symbols, []);
  assert.equal(other.answers.period, "3m");  // 뒤 카드 답은 유지
  const again = reduce(other, { type: "toggleSymbol", symbol: "ETHUSDT" });
  assert.equal(reduce(again, { type: "confirmSymbols" }).phase, "ready");
  assert.deepEqual(reduce(results, { type: "followUp", kind: "restart" }), initialState());
});

test("safer from stable is a no-op, and stable clears futures", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "balanced" },
    { type: "choose", step: "market", value: { market: "futures", leverage: 3 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "3m" }, { type: "choose", step: "interval", value: "1h" },
  );
  s = reduce(s, { type: "results", results: [], remaining: 1 });
  const safer = reduce(s, { type: "followUp", kind: "safer" });
  assert.deepEqual([safer.answers.profile, safer.answers.market, safer.answers.leverage], ["stable", "spot", 1]);
  assert.equal(reduce(safer, { type: "followUp", kind: "safer" }), safer);
  assert.equal(reduce(s, { type: "error", message: "boom" }).phase, "error");
  assert.equal(reduce(s, { type: "loading" }).phase, "loading");
});
