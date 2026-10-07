import test from "node:test";
import assert from "node:assert/strict";
import { isEvenWeights, macroSymbols, portfolioTitle, portfolioWeight } from "../src/lib/portfolio.js";

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

// ── W5 · W6. 종목 목록은 한 자리에서 읽는다 ──
test("macroSymbols — legs 형태와 symbols 형태를 같은 자리에서 읽는다", () => {
  // 비중 묶음(legs). 소비처가 macro.symbols 만 보면 단일 종목으로 그려져 '종목 비중' 행과 모순된다.
  assert.deepEqual(macroSymbols({
    symbol: "BTCUSDT",
    legs: [{ symbol: "BTCUSDT", weight: 70 }, { symbol: "ETHUSDT", weight: 30 }],
  }), ["BTCUSDT", "ETHUSDT"]);
  // 비중 없는 묶음(symbols).
  assert.deepEqual(macroSymbols({ symbol: "BTCUSDT", symbols: ["BTCUSDT", "ETHUSDT"] }), ["BTCUSDT", "ETHUSDT"]);
  // 단일 종목 · 종목 하나짜리 목록 · 빈 값.
  assert.deepEqual(macroSymbols({ symbol: "BTCUSDT" }), ["BTCUSDT"]);
  assert.deepEqual(macroSymbols({ symbol: "BTCUSDT", symbols: ["BTCUSDT"] }), ["BTCUSDT"]);
  assert.deepEqual(macroSymbols({ symbol: "BTCUSDT", legs: [{ symbol: "BTCUSDT", weight: 100 }] }), ["BTCUSDT"]);
  assert.deepEqual(macroSymbols({}), []);
  assert.deepEqual(macroSymbols(null), []);
});

// ── W7. '균등이냐 비중이냐' 판정은 한 벌 ──
test("isEvenWeights — 서버 summary 와 같은 기준(max-min < 0.02)", () => {
  assert.equal(isEvenWeights([{ weight: 50 }, { weight: 50 }]), true);
  assert.equal(isEvenWeights([{ weight: 33.33 }, { weight: 33.33 }, { weight: 33.34 }]), true, "evenWeights(3) 은 균등이다");
  assert.equal(isEvenWeights([{ weight: 70 }, { weight: 30 }]), false);
  // 경계 — 차이가 0.02 면 '균등 아님'(서버 summary.py 가 `< 0.02` 다).
  assert.equal(isEvenWeights([{ weight: 50 }, { weight: 50.02 }]), false);
  assert.equal(isEvenWeights([{ weight: 50 }, { weight: 50.01 }]), true);
  // 레그가 없거나 하나면 균등으로 본다 — 단일 종목에 '비중 지정' 이라 쓰지 않게.
  assert.equal(isEvenWeights(null), true);
  assert.equal(isEvenWeights([]), true);
  assert.equal(isEvenWeights([{ weight: 100 }]), true);
  // 숫자가 아니면 균등이라고 말하지 않는다.
  assert.equal(isEvenWeights([{ weight: "x" }, { weight: 50 }]), false);
});
