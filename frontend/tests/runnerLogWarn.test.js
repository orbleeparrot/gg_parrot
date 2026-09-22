import test from "node:test";
import assert from "node:assert/strict";
import { runnerLogModule } from "../src/features/agents/modules/runnerLog.js";

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
