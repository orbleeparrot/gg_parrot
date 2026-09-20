import assert from "node:assert/strict";
import test from "node:test";
import { STEPS, initialState, reduce, toRequest, canChooseFutures, answerLabel, periodOptions, intervalOptions, maxSymbols, isShort } from "../src/lib/askFlow.js";

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
  // safer.phase 는 이미 "ready" 라 결과 phase 가 아니어도 followUp 이 no-op 으로 보인다 —
  // 진짜로 확인하려면 결과 phase 로 옮긴 뒤에도 안정형에서 "safer" 가 no-op 인지 봐야 한다.
  const saferResults = reduce(safer, { type: "results", results: [], remaining: 1 });
  assert.equal(reduce(saferResults, { type: "followUp", kind: "safer" }), saferResults);
  assert.equal(reduce(s, { type: "error", message: "boom" }).phase, "error");
  assert.equal(reduce(s, { type: "loading" }).phase, "loading");
});

test("follow-ups other than restart are ignored outside the results phase", () => {
  const fresh = initialState();
  for (const kind of ["safer", "riskier", "symbols"]) assert.equal(reduce(fresh, { type: "followUp", kind }), fresh);
  const mid = reduce(fresh, { type: "choose", step: "profile", value: "balanced" });
  assert.equal(reduce(mid, { type: "followUp", kind: "riskier" }), mid);
  const errored = reduce(mid, { type: "error", message: "boom" });
  assert.deepEqual(reduce(errored, { type: "followUp", kind: "restart" }), initialState());
});

test("scalper unlocks short periods and intervals, and 1m needs 1w", () => {
  assert.deepEqual(periodOptions("aggressive").map((o) => o.value), ["3m", "6m", "1y"]);
  assert.deepEqual(periodOptions("scalper").map((o) => o.value), ["1w", "1m"]);
  assert.deepEqual(intervalOptions("balanced", "3m").map((o) => o.value), ["1h", "4h", "1d"]);
  const week = intervalOptions("scalper", "1w");
  assert.deepEqual(week.map((o) => [o.value, !!o.disabled]), [["1m", false], ["5m", false], ["15m", false]]);
  const month = intervalOptions("scalper", "1m");
  assert.deepEqual(month.map((o) => [o.value, !!o.disabled]), [["1m", true], ["5m", false], ["15m", false]]);
  assert.match(month[0].title, /1주/);
  assert.equal(isShort("scalper"), true);
  assert.equal(maxSymbols("scalper"), 2);
  assert.equal(maxSymbols("stable"), 3);
});

test("scalper flow validates against its own option lists", () => {
  let s = walk({ type: "choose", step: "profile", value: "scalper" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  for (const sym of ["BTCUSDT", "ETHUSDT", "SOLUSDT"]) s = reduce(s, { type: "toggleSymbol", symbol: sym });
  assert.deepEqual(s.answers.symbols, ["BTCUSDT", "ETHUSDT"]);  // 단타형은 2개까지
  s = reduce(s, { type: "confirmSymbols" });
  assert.equal(reduce(s, { type: "choose", step: "period", value: "3m" }), s);  // 긴 기간 거부
  s = reduce(s, { type: "choose", step: "period", value: "1m" });
  assert.equal(reduce(s, { type: "choose", step: "interval", value: "1m" }), s);  // 1개월 + 1분 거부
  assert.equal(reduce(s, { type: "choose", step: "interval", value: "1h" }), s);  // 긴 봉 거부
  s = reduce(s, { type: "choose", step: "interval", value: "5m" });
  assert.equal(s.phase, "ready");
  assert.deepEqual(toRequest(s.answers), { risk_profile: "scalper", market: "spot", leverage: 1, symbols: ["BTCUSDT", "ETHUSDT"], period_preset: "1m", interval: "5m" });
  // 기존 성향은 짧은 옵션을 거부
  const slow = walk({ type: "choose", step: "profile", value: "balanced" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } }, { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" });
  assert.equal(reduce(slow, { type: "choose", step: "period", value: "1w" }), slow);
});

test("riskier from aggressive becomes scalper and clears period/interval (and oversized symbols)", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "aggressive" }, { type: "choose", step: "market", value: { market: "futures", leverage: 2 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "toggleSymbol", symbol: "ETHUSDT" }, { type: "toggleSymbol", symbol: "SOLUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "3m" }, { type: "choose", step: "interval", value: "1h" },
  );
  s = reduce(s, { type: "results", results: [], remaining: 3 });
  const r = reduce(s, { type: "followUp", kind: "riskier" });
  assert.equal(r.answers.profile, "scalper");
  assert.equal(r.answers.market, "futures");  // 시장은 유지
  assert.deepEqual(r.answers.symbols, []);     // 3개 > 2개 상한 → 비움
  assert.equal(r.answers.period, null);
  assert.equal(r.answers.interval, null);
  assert.equal(r.step, "symbols");
  assert.equal(r.phase, "cards");
  assert.equal(reduce(r, { type: "followUp", kind: "riskier" }), r);  // results phase 아님 → no-op
});

test("safer from scalper becomes aggressive, keeps ≤2 symbols, clears period/interval", () => {
  let s = walk(
    { type: "choose", step: "profile", value: "scalper" }, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } },
    { type: "toggleSymbol", symbol: "BTCUSDT" }, { type: "confirmSymbols" },
    { type: "choose", step: "period", value: "1w" }, { type: "choose", step: "interval", value: "1m" },
  );
  s = reduce(s, { type: "results", results: [], remaining: 3 });
  const r = reduce(s, { type: "followUp", kind: "safer" });
  assert.equal(r.answers.profile, "aggressive");
  assert.deepEqual(r.answers.symbols, ["BTCUSDT"]);
  assert.equal(r.answers.symbolsConfirmed, true);
  assert.equal(r.answers.period, null);
  assert.equal(r.step, "period");
  assert.equal(r.phase, "cards");
  assert.equal(answerLabel("interval", { ...s.answers }), "초단타 (1분 봉)");
});
