import assert from "node:assert/strict";
import test from "node:test";
import { advanceActivityTimeline } from "../src/features/agents/activityTimeline.js";
import { runnerLogModule } from "../src/features/agents/modules/runnerLog.js";
import { macroOriginBadge } from "../src/lib/macroOrigin.js";

const session = { session_id: 8, status: "running", connected: true, in_position: false, macro_origin: "file_verified", macro_digest: "abc123def456" };
const rows = [
  { id: 3, ts: "2026-09-14T01:00:10Z", kind: "fill", message: "손익 +1.20% (+1.2 USDT)" },
  { id: 2, ts: "2026-09-14T01:00:05Z", kind: "order", message: "[진입] 100 → BUY 0.01 BTCUSDT" },
  { id: 1, ts: "2026-09-14T01:00:00Z", kind: "start", message: "실행 시작 · 실행기 v7 · 테스트넷 · 매크로 원본 파일(서명 확인)" },
];
const log = (events, session_id = 8) => ({ runner_log: { data: { session_id, events } } });
const context = { session, interval: "1m", candles: [], featureStates: log(rows), receivedAt: 5000 };

test("runner log rows become timeline events in time order, once each", () => {
  let state = advanceActivityTimeline(null, context);
  const mine = state.events.filter((e) => e.module === "runner_log");
  assert.deepEqual(mine.map((e) => e.title), [rows[2].message, rows[1].message, rows[0].message]);
  assert.deepEqual(mine.map((e) => e.severity), ["info", "signal", "signal"]);
  assert.equal(mine[1].sourceLabel, "실행기 로그 · 주문");
  assert.equal(mine[0].occurredAt, Date.parse("2026-09-14T01:00:00Z"));

  // 같은 응답을 다시 받아도(폴링) 줄이 늘지 않고, 새 줄만 붙는다.
  state = advanceActivityTimeline(state, { ...context, receivedAt: 6000 });
  assert.equal(state.events.filter((e) => e.module === "runner_log").length, 3);
  const more = [{ id: 4, ts: "2026-09-14T01:01:00Z", kind: "error", message: "일시 오류(시세): boom" }, ...rows];
  state = advanceActivityTimeline(state, { ...context, featureStates: log(more), receivedAt: 7000 });
  const after = state.events.filter((e) => e.module === "runner_log");
  assert.equal(after.length, 4);
  assert.equal(after[3].severity, "critical");
  assert.equal(after[3].notify, false);
});

test("log for another session or unknown kinds is handled defensively", () => {
  assert.deepEqual(runnerLogModule.buildEvents({ session, featureStates: log(rows, 99) }), []);
  assert.deepEqual(runnerLogModule.buildEvents({ session, featureStates: {} }), []);
  const weird = runnerLogModule.buildEvents({ session, featureStates: log([{ id: 1, ts: "", kind: "???", message: "x" }]) });
  assert.equal(weird[0].sourceLabel, "실행기 로그 · 실행기");
  assert.equal(weird[0].severity, "info");
});

test("macro origin badge maps server origins to labels and tones", () => {
  assert.equal(macroOriginBadge({ macro_origin: "" }), null);
  assert.equal(macroOriginBadge(null), null);
  const verified = macroOriginBadge(session);
  assert.equal(verified.label, "원본 파일");
  assert.equal(verified.className, "badge badge-mine");
  assert.match(verified.title, /지문 abc123def456/);
  const modified = macroOriginBadge({ macro_origin: "file_modified" });
  assert.equal(modified.label, "수정된 파일");
  assert.equal(modified.className, "badge badge-risk");
  assert.equal(macroOriginBadge({ macro_origin: "web" }).label, "웹에서 바로 실행");
  assert.equal(macroOriginBadge({ macro_origin: "file_unsigned" }).tone, "flat");
});
