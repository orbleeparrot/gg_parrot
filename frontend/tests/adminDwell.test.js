import test from "node:test";
import assert from "node:assert/strict";
import { measuredSessionAverage } from "../src/lib/adminFormat.js";

test("period mean uses raw dwell and only measured sessions, not daily means or all visits", () => {
  const days = [
    { sessions: 100, measured_sessions: 1, session_dwell_ms: 10000, avg_session_sec: 10 },
    { sessions: 9, measured_sessions: 9, session_dwell_ms: 900000, avg_session_sec: 100 },
    { sessions: 200, measured_sessions: 0, session_dwell_ms: 0, avg_session_sec: null },
  ];
  assert.equal(measuredSessionAverage(days), 91);
  assert.equal(measuredSessionAverage(days.slice(0, 1)), 10);
});

test("round once like backend, including half-to-even seconds; zero is measured", () => {
  assert.equal(measuredSessionAverage([{ measured_sessions: 1, session_dwell_ms: 2500 }]), 2);
  assert.equal(measuredSessionAverage([{ measured_sessions: 1, session_dwell_ms: 3500 }]), 4);
  assert.equal(measuredSessionAverage([{ measured_sessions: 1, session_dwell_ms: 0 }]), 0);
  assert.equal(measuredSessionAverage([{ measured_sessions: 0, session_dwell_ms: 0 }]), null);
});

test("missing or invalid raw measurements never fabricate a period mean", () => {
  assert.equal(measuredSessionAverage([]), null);
  assert.equal(measuredSessionAverage([{ avg_session_sec: 10, sessions: 1 }]), null);
  assert.equal(measuredSessionAverage([{ measured_sessions: 1, session_dwell_ms: 1000 }, {}]), null);
  assert.equal(measuredSessionAverage([{ measured_sessions: 1, session_dwell_ms: -1 }]), null);
});
