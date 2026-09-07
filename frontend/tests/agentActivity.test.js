import assert from "node:assert/strict";
import test from "node:test";

import { executionModule } from "../src/features/agents/modules/execution.js";
import { riskModule } from "../src/features/agents/modules/risk.js";

test("a routine connected heartbeat never produces a chat notification", () => {
  const session = { session_id: 8, status: "running", connected: true, in_position: true };
  assert.deepEqual(executionModule.buildEvents({ session }), []);
  assert.deepEqual(executionModule.buildEvents({ session: { ...session, unrealized_pct: 0.12 }, previousSession: session }), []);
});

test("normal volatility and small PnL changes stay out of the notification feed", () => {
  const candles = [{ t: 1000, o: 100, h: 100.2, l: 99.8, c: 100.1, closed: true }];
  for (const unrealized_pct of [0, 0.1, 0.2, -0.1, -1]) {
    assert.deepEqual(riskModule.buildEvents({ candles, interval: "1m", session: {
      session_id: 8, status: "running", in_position: true, unrealized_pct,
    } }), []);
  }
});

test("an uncertain order never appears as a confirmed closed position", () => {
  const [event] = executionModule.buildEvents({ session: {
    session_id: 8, status: "stopped", in_position: false, position_uncertain: true,
  } });
  assert.equal(event.title, "포지션 확인 필요");
  assert.equal(event.severity, "critical");
  assert.match(event.summary, /거래소/);
});

test("a close-and-stop request is visible before the runner confirms completion", () => {
  const [event] = executionModule.buildEvents({ session: {
    session_id: 8, status: "running", connected: true,
    stopping: true, stop_mode: "close_and_stop", in_position: true,
  } });
  assert.match(event.title, /청산.*종료.*요청/);
  assert.match(event.summary, /확인/);
});

test("position loss alerts remain available when chart candles fail to load", () => {
  const events = riskModule.buildEvents({ candles: [], session: {
    session_id: 8, status: "running", in_position: true, unrealized_pct: -3,
  } });
  assert.equal(events.length, 1);
  assert.equal(events[0].severity, "critical");
  assert.match(events[0].summary, /-3.00/);
});
