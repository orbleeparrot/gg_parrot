// 매크로 카드가 종목 비중과 묶음 한도를 구조화된 칸으로 보여 주는지 — 서버 요약 끝 문구는 가장 먼저 잘리므로 이 칸이 본진이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";
import { weightPhrase, bundleLimitPhrase, baseTicker } from "../src/lib/portfolio.js";

const MACRO = {
  exchange: "binance", symbol: "BTCUSDT", rule_type: "I", candle_interval: "1h",
  params: { k: 0.5, initial_capital: 1000 }, risk: { invest_ratio: 1 },
  period: { preset: "3m" },
};
const card = async (macro, symbols) =>
  textOf(await renderComponent("src/components/MacroCard.jsx", { macro, symbols }));

test("weightPhrase 는 base 티커와 비중을 잇는다", () => {
  assert.equal(
    weightPhrase([{ symbol: "BTCUSDT", weight: 50 }, { symbol: "ETHUSDT", weight: 30 },
                  { symbol: "SOLUSDT", weight: 20 }]),
    "BTC 50% · ETH 30% · SOL 20%",
  );
  assert.equal(weightPhrase([{ symbol: "BTCUSDT", weight: 33.33 }, { symbol: "ETHUSDT", weight: 66.67 }]),
    "BTC 33.33% · ETH 66.67%");
  assert.equal(weightPhrase([]), "");
  assert.equal(weightPhrase(null), "");
});

test("baseTicker 는 원화 · 달러 마켓 꼬리를 뗀다", () => {
  assert.equal(baseTicker("BTCUSDT"), "BTC");
  assert.equal(baseTicker("XRPKRW"), "XRP");
  assert.equal(baseTicker("ETHBUSD"), "ETH");
  assert.equal(baseTicker("btcusdt"), "BTC");
});

test("bundleLimitPhrase 는 서버 BundleGate.note() 와 같은 문구를 낸다", () => {
  assert.equal(bundleLimitPhrase({ max_positions: 1 }), "한 번에 1종목까지");
  assert.equal(bundleLimitPhrase({ max_positions: 3 }), "한 번에 3종목까지");
  assert.equal(bundleLimitPhrase({ max_exposure_pct: 60 }), "총 노출 60% 까지");
  assert.equal(bundleLimitPhrase({ max_exposure_pct: 62.5 }), "총 노출 62.5% 까지");
  assert.equal(bundleLimitPhrase({ max_positions: 2, max_exposure_pct: 80 }), "한 번에 2종목까지 · 총 노출 80% 까지");
  assert.equal(bundleLimitPhrase({ max_exposure_pct: 100 }), "총 노출 100% 까지");
  assert.equal(bundleLimitPhrase(null), "");
  assert.equal(bundleLimitPhrase({}), "");
});

test("비중이 다르면 카드가 비중을 적는다", async () => {
  const text = await card(
    { ...MACRO, legs: [{ symbol: "BTCUSDT", weight: 70 }, { symbol: "ETHUSDT", weight: 30 }] },
    ["BTCUSDT", "ETHUSDT"]);
  assert.match(text, /종목 비중/);
  assert.match(text, /BTC 70% · ETH 30%/);
});

test("비중이 다르면 '자금 균등' · '종목당 1/N' 같은 거짓 문구가 없다", async () => {
  const text = await card(
    { ...MACRO, legs: [{ symbol: "BTCUSDT", weight: 70 }, { symbol: "ETHUSDT", weight: 30 }] },
    ["BTCUSDT", "ETHUSDT"]);
  assert.doesNotMatch(text, /자금 균등/);
  assert.doesNotMatch(text, /종목당/);
  assert.match(text, /2종목 비중 지정/);
});

test("균등이면 기존 문구 그대로", async () => {
  const text = await card({ ...MACRO, symbols: ["BTCUSDT", "ETHUSDT"] }, ["BTCUSDT", "ETHUSDT"]);
  assert.match(text, /자금 균등/);
  assert.match(text, /종목당 1\/2/);
  assert.doesNotMatch(text, /종목 비중/);
});

test("묶음 한도가 있으면 한 행으로 보인다", async () => {
  const text = await card(
    { ...MACRO, legs: [{ symbol: "BTCUSDT", weight: 50 }, { symbol: "ETHUSDT", weight: 50 }],
      bundle_risk: { max_positions: 1, max_exposure_pct: 60 } },
    ["BTCUSDT", "ETHUSDT"]);
  assert.match(text, /묶음 한도/);
  assert.match(text, /한 번에 1종목까지 · 총 노출 60% 까지/);
});

test("묶음 한도는 비중 없는 균등 묶음에도 보인다", async () => {
  const text = await card(
    { ...MACRO, symbols: ["BTCUSDT", "ETHUSDT"], bundle_risk: { max_exposure_pct: 62.5 } },
    ["BTCUSDT", "ETHUSDT"]);
  assert.match(text, /묶음 한도/);
  assert.match(text, /총 노출 62\.5% 까지/);
  assert.match(text, /자금 균등/);
});

test("단일 종목 카드에는 묶음 행이 없다", async () => {
  const text = await card(MACRO, ["BTCUSDT"]);
  assert.doesNotMatch(text, /묶음 한도|종목 비중/);
});
