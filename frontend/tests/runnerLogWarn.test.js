import test from "node:test";
import assert from "node:assert/strict";
import { runnerLogModule } from "../src/features/agents/modules/runnerLog.js";
import { advanceActivityTimeline } from "../src/features/agents/activityTimeline.js";

const session = { session_id: 1 };
const featureStates = {
  runner_log: {
    data: {
      session_id: 1,
      events: [{ id: 9, kind: "warn", ts: "2026-09-22T00:00:00Z", message: "포지션 불일치" }],
    },
  },
};

test("warn 종류는 '주의' 라벨과 warning 심각도로 그려진다", () => {
  const [ev] = runnerLogModule.buildEvents({ session, featureStates });
  assert.equal(ev.severity, "warning");
  assert.equal(ev.expression, "warning");
  assert.match(ev.sourceLabel, /주의/);
});

const retryMessage = "서버 연결 재시도 중 — 신호 대기(진입 없음, 손절만 로컬에서 봅니다)";
const retryLog = { runner_log: { data: { session_id: 1, events: [{ id: 10, kind: "signal",
  ts: "2026-10-08T06:33:00Z", message: retryMessage }] } } };

test("a confirmed later heartbeat marks a historical server retry log recovered", () => {
  const [event] = runnerLogModule.buildEvents({ session: { ...session, status: "running", connected: true,
    last_heartbeat_at: "2026-10-08T06:34:00Z" }, featureStates: retryLog });
  assert.match(event.title, /복구/);
  assert.doesNotMatch(event.title, /재시도 중|진입 없음/);
  assert.equal(event.severity, "info");
  assert.equal(event.originalTitle, retryMessage);
  assert.equal(event.resolvedAt, Date.parse("2026-10-08T06:34:00Z"));
  assert.equal(event.occurredAt, "2026-10-08T06:33:00Z");
});

test("a connected flag or an older, invalid or equal heartbeat cannot prove retry recovery", () => {
  for (const heartbeat of [undefined, "bad", "2026-10-08T06:32:00Z", "2026-10-08T06:33:00Z"]) {
    const [event] = runnerLogModule.buildEvents({ session: { ...session, connected: true,
      last_heartbeat_at: heartbeat }, featureStates: retryLog });
    assert.equal(event.title, retryMessage);
    assert.equal(event.resolvedAt, undefined);
  }
});

test("a seen retry log updates in place when a later heartbeat confirms recovery", () => {
  const context = { session: { ...session, status: "running", connected: true },
    featureStates: retryLog, interval: "1m", candles: [], receivedAt: 1000 };
  let timeline = advanceActivityTimeline(null, context);
  assert.equal(timeline.events.length, 1);
  assert.equal(timeline.events[0].title, retryMessage);
  const originalId = timeline.events[0].id;
  const originalTime = timeline.events[0].occurredAt;
  timeline = advanceActivityTimeline(timeline, { ...context, receivedAt: 2000,
    session: { ...context.session, last_heartbeat_at: "2026-10-08T06:34:00Z" } });
  assert.equal(timeline.events.length, 1);
  assert.equal(timeline.events[0].id, originalId);
  assert.equal(timeline.events[0].occurredAt, originalTime);
  assert.match(timeline.events[0].title, /복구/);
  assert.equal(timeline.events[0].resolvedAt, Date.parse("2026-10-08T06:34:00Z"));
});
