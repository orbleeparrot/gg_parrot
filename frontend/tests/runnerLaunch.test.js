import assert from "node:assert/strict";
import test from "node:test";

import { findLaunchedSession, launchPhaseFromTicketStatus } from "../src/lib/runnerLaunch.js";

const selected = { id: 42, symbol: "ZECUSDT" };

test("a new running session for the chosen macro is the launched one", () => {
  const active = [
    { session_id: 1, status: "running", user_macro_id: 42, symbol: "ZECUSDT" },
    { session_id: 2, status: "running", user_macro_id: 42, symbol: "ZECUSDT" },
  ];
  assert.equal(findLaunchedSession(active, new Set([1]), selected)?.session_id, 2);
});

test("sessions that were already running before the launch step are ignored", () => {
  const active = [{ session_id: 1, status: "running", user_macro_id: 42, symbol: "ZECUSDT" }];
  assert.equal(findLaunchedSession(active, new Set([1]), selected), null);
});

test("another macro or a stopped session never counts", () => {
  const active = [
    { session_id: 3, status: "running", user_macro_id: 7, symbol: "BTCUSDT" },
    { session_id: 4, status: "stopped", user_macro_id: 42, symbol: "ZECUSDT" },
  ];
  assert.equal(findLaunchedSession(active, new Set(), selected), null);
});

test("legacy runners without a macro id match by symbol only", () => {
  const active = [
    { session_id: 5, status: "running", user_macro_id: null, symbol: "ZECUSDT" },
    { session_id: 6, status: "running", user_macro_id: null, symbol: "BTCUSDT" },
  ];
  assert.equal(findLaunchedSession(active, [], selected)?.session_id, 5);
  assert.equal(findLaunchedSession(active, [], { id: 9, symbol: "ETHUSDT" }), null);
});

test("a rejected launch ticket becomes an 'outdated runner' phase with both versions", () => {
  const outdated = launchPhaseFromTicketStatus({ status: "rejected", runner_version: "5", min_runner_version: "6" });
  assert.deepEqual(outdated, { phase: "outdated", runnerVersion: "5", minVersion: "6" });
  assert.deepEqual(launchPhaseFromTicketStatus({ status: "claimed" }), { phase: "claimed" });
  assert.deepEqual(launchPhaseFromTicketStatus({ status: "expired" }), { phase: "expired" });
  assert.deepEqual(launchPhaseFromTicketStatus({ status: "ready" }), { phase: null });
  assert.deepEqual(launchPhaseFromTicketStatus(null), { phase: null });
});
