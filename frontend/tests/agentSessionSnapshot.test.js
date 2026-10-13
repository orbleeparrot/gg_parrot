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

import { newlyStartedSession } from "../src/features/agents/sessionSnapshot.js";

test("결과 화면에서 새로 시작한 세션을 고른다 — 보던 것과 같은 매크로 먼저, 이미 알던 세션은 아니다", () => {
  const shown = { session_id: 10, user_macro_id: 5, status: "stopped" };
  const known = new Set([20]);
  const active = [
    { session_id: 20, user_macro_id: 9, status: "running" },
    { session_id: 31, user_macro_id: 7, status: "running" },
    { session_id: 32, user_macro_id: 5, status: "running" },
  ];
  assert.equal(newlyStartedSession(known, active, shown).session_id, 32);
  assert.equal(newlyStartedSession(known, active.slice(0, 2), shown).session_id, 31);
  assert.equal(newlyStartedSession(new Set([20, 31, 32]), active, shown), null);
  assert.equal(newlyStartedSession(known, [{ session_id: 40, status: "stopped" }], shown), null);
});
