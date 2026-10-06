import assert from "node:assert/strict";
import test from "node:test";
import { activeSessionChart, requireSessionSnapshot } from "../src/features/agents/sessionSnapshot.js";

test("missing chart/session is safe during anonymous, loading, empty and failed states", () => {
  for (const [chart, session] of [[null, null], [undefined, undefined], [null, {}], [{}, null], [{}, {}]]) {
    assert.equal(activeSessionChart(chart, session), null);
  }
});

test("only a chart matching the selected symbol and exchange is reused", () => {
  const session = { symbol: "KRW-BTC" };
  const chart = { symbol: "KRW-BTC", exchange: "upbit", candles: [] };
  assert.equal(activeSessionChart(chart, session, "upbit"), chart);
  assert.equal(activeSessionChart(chart, session, "bithumb"), null);
  assert.equal(activeSessionChart(chart, { symbol: "KRW-ETH" }, "upbit"), null);
  assert.equal(activeSessionChart(null, session, "upbit"), null);
  const legacy = { symbol: "BTCUSDT" };
  assert.equal(activeSessionChart(legacy, { symbol: "BTCUSDT" }), legacy);
});

test("valid empty and populated snapshots preserve the server result", () => {
  for (const snapshot of [{ active: [], recent: [] }, { active: [{ session_id: 1 }], recent: [], history_policy: { keep: 30 } }]) {
    assert.equal(requireSessionSnapshot(snapshot), snapshot);
  }
});

test("null, malformed lists and null rows become recoverable request errors", () => {
  for (const snapshot of [null, undefined, {}, { active: null, recent: [] }, { active: [], recent: {} }, { active: [null], recent: [] }, { active: [], recent: [null] }]) {
    assert.throws(() => requireSessionSnapshot(snapshot), /다시 불러와/);
  }
});
