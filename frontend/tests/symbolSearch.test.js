import test from "node:test";
import assert from "node:assert/strict";
import { marketTags, resolveSymbol, searchSymbols } from "../src/lib/symbolSearch.js";

const ITEMS = [
  { symbol: "BTCUSDT", base: "BTC", quote: "USDT", spot: true, futures: true },
  { symbol: "ETHUSDT", base: "ETH", quote: "USDT", spot: true, futures: true },
  { symbol: "CHIPUSDT", base: "CHIP", quote: "USDT", spot: true, futures: true },
  { symbol: "1000PEPEUSDT", base: "1000PEPE", quote: "USDT", spot: false, futures: true },
  { symbol: "PEPEUSDT", base: "PEPE", quote: "USDT", spot: true, futures: false },
  { symbol: "BCHUSDT", base: "BCH", quote: "USDT", spot: true, futures: true },
];

test("검색 — base 일치가 먼저, 그다음 앞머리, 그다음 포함", () => {
  assert.deepEqual(searchSymbols(ITEMS, "pepe").map((i) => i.symbol), ["PEPEUSDT", "1000PEPEUSDT"]);
  assert.deepEqual(searchSymbols(ITEMS, "b").map((i) => i.symbol), ["BCHUSDT", "BTCUSDT"]);
  assert.deepEqual(searchSymbols(ITEMS, "btcusdt").map((i) => i.symbol), ["BTCUSDT"]);
});

test("검색 — 대소문자·공백·기호를 무시하고, 이미 고른 종목은 뺀다", () => {
  assert.deepEqual(searchSymbols(ITEMS, " Chip ").map((i) => i.symbol), ["CHIPUSDT"]);
  assert.deepEqual(searchSymbols(ITEMS, "btc", { exclude: ["BTCUSDT"] }), []);
  assert.deepEqual(searchSymbols(ITEMS, ""), []);
  assert.equal(searchSymbols(ITEMS, "e", { limit: 1 }).length, 1);
});

test("resolveSymbol — base 만 쳐도 USDT 쌍으로, 없는 글자는 null", () => {
  assert.equal(resolveSymbol(ITEMS, "chip"), "CHIPUSDT");
  assert.equal(resolveSymbol(ITEMS, "CHIPUSDT"), "CHIPUSDT");
  assert.equal(resolveSymbol(ITEMS, "1000pepe"), "1000PEPEUSDT");
  assert.equal(resolveSymbol(ITEMS, "DOGE"), null);
  assert.equal(resolveSymbol(ITEMS, ""), null);
});

test("marketTags — 시장 표시", () => {
  assert.deepEqual(marketTags(ITEMS[0]), ["현물", "선물"]);
  assert.deepEqual(marketTags(ITEMS[3]), ["선물"]);
  assert.deepEqual(marketTags(ITEMS[4]), ["현물"]);
});
