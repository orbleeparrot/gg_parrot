import assert from "node:assert/strict";
import test from "node:test";

import { tierNextLabel, tierStepAt, tierSteps } from "../src/lib/profileText.js";
import { canJoin } from "../src/lib/roomFlow.js";

test("관리자 등급은 다이아 위 전용 칸이 지금 등급이다", () => {
  const steps = tierSteps({ admin: true, name: "관리자", sales: 0 });
  assert.equal(steps.at(-1).name, "관리자");
  assert.equal(steps.at(-1).state, "current");
  assert.ok(steps.slice(0, -1).every((step) => step.state === "done"));
  assert.equal(tierNextLabel({ admin: true }), "관리자 전용 등급이에요");
  assert.equal(tierStepAt(null), "관리자 계정");
});

test("관리자는 포인트가 없어도 유료방에 들어갈 수 있다(입장료를 내지 않는다)", () => {
  const room = { is_member: false, is_open: true, expires_ms: Date.now() + 3600_000, member_count: 1, capacity: 10, entry_fee: 100 };
  const old = Date.now() - 10 * 86_400_000;
  assert.equal(canJoin(room, { points_balance: 0, created_ms: old }).ok, false);
  assert.equal(canJoin(room, { points_balance: 0, points_unlimited: true, created_ms: old }).ok, true);
});
