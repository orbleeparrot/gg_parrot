// 진입 필터가 화면에 보이는가 — 서버는 필터 구를 요약 한 줄의 맨 끝에 붙이고, 그 끝이 말줄임에 가장 먼저 잘린다.
// 그래서 필터는 구조화된 자리(카드 사양표 · 리더보드 전략 칸의 따로 놓인 칸)에도 있어야 한다.
// 소스에 글자가 있느냐가 아니라, 실제로 그린 화면에 그 칸이 있느냐를 본다.
import assert from "node:assert/strict";
import { test } from "node:test";
import { buildMacro, defaultForm, entryFilterPhrase, withTypeDefaults } from "../src/lib/macro.js";
import { leaderboardStrategy } from "../src/lib/leaderboardStrategy.js";
import { renderComponent, textOf } from "./renderHelper.js";

// 서버(backend/app/engine/entry_filter.py note())의 문구 그대로 — 두 화면이 서로 다른 말을 하지 않게.
const SERVER_WORDING = {
  ma: [{ kind: "ma", params: { ma_type: "SMA", period: 20, side: "above" } }, "20봉 SMA 이동평균 위"],
  rsi: [{ kind: "rsi", params: { period: 14, max: 70 } }, "RSI(14) 70 이하"],
  bb: [{ kind: "bb", params: { period: 20, num_std: 2, zone: "inside" } }, "볼린저(20, 2σ) 밴드 안"],
  volume: [{ kind: "volume", params: { period: 20, multiple: 2 } }, "거래량이 20봉 평균의 2배 이상"],
};

const macroWith = (entry_filter) => ({
  ...buildMacro({ ...withTypeDefaults({ ...defaultForm(), symbol: "BTCUSDT" }, "I"), use_entry_filter: false }),
  entry_filter,
});
// 서버가 실제로 내는 모양: 필터 구는 맨 끝.
const summaryOf = (phrase) => `BTC · 롱 · 변동성 돌파 k=0.5 · -3% 손절 · 자금 100% 투입${phrase ? ` · 진입 조건: ${phrase}` : ""}`;
const entryOf = (entry_filter) => ({
  symbol: "BTCUSDT", locked: false, macro: macroWith(entry_filter),
  human_summary: summaryOf(entry_filter ? entryFilterPhrase(entry_filter) : null),
});
const RESULT = { final_return_pct: 12.3, mdd_pct: 4.5, win_rate_pct: 55, total_trades: 9, initial_capital: 1000, equity_curve: [] };

test("진입 조건 구는 서버 요약과 같은 말이다 — 네 종류", () => {
  for (const [kind, [filter, phrase]] of Object.entries(SERVER_WORDING)) {
    assert.equal(entryFilterPhrase(filter), phrase, kind);
  }
});

test("RSI 는 한쪽만 · 양쪽 다, 서버가 비운 쪽을 null 로 되돌려 줘도 같은 말이다", () => {
  assert.equal(entryFilterPhrase({ kind: "rsi", params: { period: 9, min: 30, max: null } }), "RSI(9) 30 이상");
  assert.equal(entryFilterPhrase({ kind: "rsi", params: { period: 14, min: null, max: 70 } }), "RSI(14) 70 이하");
  assert.equal(entryFilterPhrase({ kind: "rsi", params: { period: 14, min: 0, max: 100 } }), "RSI(14) 0~100");
  assert.equal(entryFilterPhrase({ kind: "rsi", params: { period: 14, min: 0, max: null } }), "RSI(14) 0 이상");
});

test("필터가 없거나 모르는 종류면 구를 만들지 않는다", () => {
  assert.equal(entryFilterPhrase(null), null);
  assert.equal(entryFilterPhrase(undefined), null);
  assert.equal(entryFilterPhrase({ kind: "ref", params: { period: 50 } }), null);
});

test("리더보드 전략 값은 진입 조건을 따로 내보내고, 요약 끝의 구도 그대로 둔다", () => {
  for (const [kind, [filter, phrase]] of Object.entries(SERVER_WORDING)) {
    const data = leaderboardStrategy(entryOf(filter));
    assert.equal(data.entryCondition, phrase, kind);
    assert.ok(data.description.endsWith(`진입 조건: ${phrase}`), `${kind}: 요약 끝의 구가 사라졌다`);
  }
});

test("필터가 없는 전략 값에는 진입 조건 키가 없다", () => {
  const data = leaderboardStrategy(entryOf(null));
  assert.equal("entryCondition" in data, false);
  assert.doesNotMatch(data.description, /진입 조건/);
});

test("리더보드 전략 칸: 진입 조건이 따로 그려지고, 말줄임에 가장 먼저 잘리는 요약 끝보다 앞에 놓인다", async () => {
  for (const [kind, [filter, phrase]] of Object.entries(SERVER_WORDING)) {
    const html = await renderComponent("src/components/StrategyDetails.jsx", { entry: entryOf(filter) });
    const cell = html.match(/<div class="lb-fact lb-fact-entry">(.*?)<\/div>/);
    assert.ok(cell, `${kind}: 진입 조건 칸이 안 그려졌다`);
    assert.equal(textOf(cell[1]), `진입 조건 ${phrase}`, kind);
    assert.ok(html.indexOf("lb-fact-entry") < html.indexOf("lb-fact-strategy"), `${kind}: 요약보다 뒤에 놓이면 말줄임에 같이 잘린다`);
    assert.ok(html.indexOf("lb-fact-entry") < html.indexOf("lb-fact-capital"), kind);
  }
});

test("리더보드 전략 칸: 한 줄 전문(title)과 요약 문장에도 진입 조건이 실려 있다", async () => {
  const [filter, phrase] = SERVER_WORDING.volume;
  const html = await renderComponent("src/components/StrategyDetails.jsx", { entry: entryOf(filter) });
  assert.match(html, new RegExp(`title="[^"]*진입 조건 ${phrase}[^"]*"`));
  assert.match(textOf(html.match(/<dd class="lb-summary-text">(.*?)<\/dd>/)[1]), new RegExp(`진입 조건: ${phrase}$`));
});

test("리더보드 전략 칸: 필터가 없는 전략과 잠긴 전략에는 진입 조건 칸이 없다", async () => {
  const none = await renderComponent("src/components/StrategyDetails.jsx", { entry: entryOf(null) });
  assert.doesNotMatch(none, /lb-fact-entry/);
  assert.doesNotMatch(textOf(none), /진입 조건/);
  const locked = await renderComponent("src/components/StrategyDetails.jsx", { entry: { ...entryOf(SERVER_WORDING.ma[0]), locked: true } });
  assert.doesNotMatch(textOf(locked), /진입 조건/);
});

test("매크로 카드 사양표: 진입 조건 줄이 있고, 요약 문장이 잘려 필터 구가 없어도 보인다", async () => {
  for (const [kind, [filter, phrase]] of Object.entries(SERVER_WORDING)) {
    // human_summary 에서 필터 구를 일부러 뺀다 — 말줄임에 잘린 뒤와 같다. 사양표는 요약 문자열이 아니라 매크로 값을 읽는다.
    const strategyEntry = { ...entryOf(filter), human_summary: summaryOf(null) };
    const html = await renderComponent("src/components/MacroCard.jsx", { macro: strategyEntry.macro, result: RESULT, strategyEntry, symbols: ["BTCUSDT"] });
    const facts = html.match(/<dl class="sd-card-facts">(.*?)<\/dl>/)[1];
    assert.ok(textOf(facts).includes(`진입 조건 ${phrase}`), `${kind}: ${textOf(facts)}`);
  }
});

test("매크로 카드 사양표: 필터가 없는 매크로에는 진입 조건 줄이 없다", async () => {
  const strategyEntry = entryOf(null);
  const html = await renderComponent("src/components/MacroCard.jsx", { macro: strategyEntry.macro, result: RESULT, strategyEntry, symbols: ["BTCUSDT"] });
  assert.doesNotMatch(html.match(/<dl class="sd-card-facts">(.*?)<\/dl>/)[1], /진입 조건/);
});
