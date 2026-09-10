import test from "node:test";
import assert from "node:assert/strict";
import { applyMacroPick, filterMacros, slashQuery } from "../src/lib/chatSlash.js";

const entries = [
  { id: 1, symbol: "BTCUSDT", username: "노희재", human_summary: "20일선 돌파 매수" },
  { id: 2, symbol: "ETHUSDT", username: "코인고래", human_summary: "RSI 30 이하 매수" },
  { id: 3, symbol: "BTCUSDT", username: "parrot_02", human_summary: "볼린저 하단 터치" },
];

test("slashQuery reads the query only when the whole input starts with a slash", () => {
  assert.equal(slashQuery("/"), "");
  assert.equal(slashQuery("/btc"), "btc");
  assert.equal(slashQuery("안녕 /btc"), null); // 글 가운데 슬래시는 그냥 글자
  assert.equal(slashQuery(""), null);
});

test("filterMacros matches symbol, author and strategy, and caps the list", () => {
  assert.deepEqual(filterMacros(entries, "").map((e) => e.id), [1, 2, 3]);
  assert.deepEqual(filterMacros(entries, "btc").map((e) => e.id), [1, 3]);
  assert.deepEqual(filterMacros(entries, "고래").map((e) => e.id), [2]);
  assert.deepEqual(filterMacros(entries, "볼린저").map((e) => e.id), [3]);
  assert.deepEqual(filterMacros(entries, "없는말"), []);
  assert.equal(filterMacros(entries, "", 2).length, 2);
});

test("applyMacroPick replaces the slash query with the token", () => {
  assert.equal(applyMacroPick("/btc", 3), "[macro:3] ");
  assert.equal(applyMacroPick("/", 1), "[macro:1] ");
  assert.equal(applyMacroPick("이거 보세요", 5), "이거 보세요 [macro:5] ");
  assert.equal(applyMacroPick("", 5), "[macro:5] ");
});
