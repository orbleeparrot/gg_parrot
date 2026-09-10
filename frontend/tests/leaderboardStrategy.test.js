import test from "node:test";
import assert from "node:assert/strict";
import { leaderboardStrategy } from "../src/lib/leaderboardStrategy.js";

const entry = (overrides = {}) => ({
  symbol: "BTCUSDT", locked: false,
  macro: { rule_type: "A", position_side: "long", risk: { invest_ratio: .375 }, leverage: 3, margin_mode: "isolated" },
  human_summary: "BTC · 롱 · 평단 대비 +5% 익절 · -3% 손절 후 재진입 · 자금 37.5% 투입 · 3배 레버리지(격리)",
  ...overrides,
});

test("separates values while preserving all strategy conditions", () => {
  assert.deepEqual(leaderboardStrategy(entry()), {
    side: "long", description: "평단 대비 +5% 익절 · -3% 손절 후 재진입 · 3배 레버리지(격리)",
    capital: { label: "자금", value: "37.5%", unit: "투입" },
  });
});
test("locked entries never expose even accidentally supplied details", () => {
  assert.equal(leaderboardStrategy(entry({ locked: true })), null);
});
test("DCA shows amount per buy in its quote currency, not a portfolio percentage", () => {
  const data = leaderboardStrategy(entry({ symbol: "ETHUSDC", human_summary: "ETH · 롱 · 7일마다 25.5 분할매수(DCA)",
    macro: { rule_type: "C", position_side: "long", params: { amount_per_buy: 25.5 }, risk: { invest_ratio: 1 } } }));
  assert.equal(data.description, "7일마다 25.5 분할매수(DCA)");
  assert.deepEqual(data.capital, { label: "회당 자금", value: "25.5", unit: "USDC" });
});
test("K distinguishes a long-to-short transition from a long-only variant", () => {
  const macro = { rule_type: "K", position_side: "long", params: {} };
  assert.equal(leaderboardStrategy(entry({ macro })).side, "switch");
  assert.equal(leaderboardStrategy(entry({ macro: { ...macro, params: { flip_to_short: false } } })).side, "long");
});
test("the short position comes from the macro", () => {
  const value = entry();
  value.macro = { ...value.macro, position_side: "short", margin_mode: "cross" };
  const data = leaderboardStrategy(value);
  assert.equal(data.side, "short");
});
test("legacy prose and missing macro are preserved without invented values", () => {
  const data = leaderboardStrategy(entry({ macro: null, human_summary: "수익률이 5%에 도달하면 매도해요." }));
  assert.equal(data.description, "수익률이 5%에 도달하면 매도해요.");
  assert.equal(data.side, null);
  assert.equal(data.capital, null);
});
test("invalid or absent capital does not become NaN or a default 100%", () => {
  for (const invest_ratio of [null, undefined, "invalid", 0]) {
    assert.equal(leaderboardStrategy(entry({ macro: { risk: { invest_ratio } } })).capital, null);
  }
});
