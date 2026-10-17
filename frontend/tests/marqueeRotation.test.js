import assert from "node:assert/strict";
import test from "node:test";

import { nextMarqueeExchange, shownMarqueeExchange } from "../src/lib/marqueeRotation.js";

const coin = { symbol: "X" };

test("the racer strip rotates upbit → bithumb → binance and back", () => {
  const lists = { upbit: [coin], bithumb: [coin], binance: [coin] };
  assert.equal(nextMarqueeExchange("upbit", lists), "bithumb");
  assert.equal(nextMarqueeExchange("bithumb", lists), "binance");
  assert.equal(nextMarqueeExchange("binance", lists), "upbit");
});

test("exchanges with no list yet are skipped, and a lone exchange stays", () => {
  assert.equal(nextMarqueeExchange("upbit", { upbit: [coin], bithumb: [], binance: [coin] }), "binance");
  assert.equal(shownMarqueeExchange("bithumb", { upbit: [coin], bithumb: [], binance: [coin] }), "binance");
  assert.equal(nextMarqueeExchange("upbit", { upbit: [coin], bithumb: [], binance: [] }), "upbit");
  assert.equal(shownMarqueeExchange("upbit", { upbit: [], bithumb: [], binance: [] }), "");
});
