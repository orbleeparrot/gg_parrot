import test from "node:test";
import assert from "node:assert/strict";
import { portfolioTitle, portfolioWeight } from "../src/lib/portfolio.js";

test("제목 — 하나면 base, 셋까지는 가운뎃점, 넷부터는 '외 n종목'", () => {
  assert.equal(portfolioTitle(["BTCUSDT"]), "BTC");
  assert.equal(portfolioTitle(["BTCUSDT", "ETHUSDT", "SOLUSDT"]), "BTC · ETH · SOL");
  assert.equal(portfolioTitle(["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]), "BTC · ETH 외 2종목");
  assert.equal(portfolioTitle([]), "");
});

test("비중 — 균등 분할", () => {
  assert.deepEqual(portfolioWeight(1), { fraction: "1/1", percent: "100%" });
  assert.deepEqual(portfolioWeight(2), { fraction: "1/2", percent: "50%" });
  assert.deepEqual(portfolioWeight(3), { fraction: "1/3", percent: "33%" });
  assert.deepEqual(portfolioWeight(0), { fraction: "1/1", percent: "100%" });
});
