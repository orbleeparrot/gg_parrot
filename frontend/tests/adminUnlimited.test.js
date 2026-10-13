import assert from "node:assert/strict";
import test from "node:test";

import { tierNextLabel, tierSteps } from "../src/lib/profileText.js";
import { canJoin } from "../src/lib/roomFlow.js";

test("판매 등급 안내(사다리)에는 관리자 칸이 없다 — 관리자도 실제 판매 수로 위치를 본다", () => {
  const steps = tierSteps({ admin: true, name: "관리자", sales: 6, ladder: [
    { name: "새싹", at: 0 }, { name: "브론즈", at: 1 }, { name: "실버", at: 5 }, { name: "골드", at: 15 }, { name: "다이아", at: 40 }] });
  assert.ok(steps.every((step) => step.name !== "관리자"));
  assert.equal(steps.find((step) => step.state === "current").name, "실버");
  assert.equal(tierNextLabel({ admin: true, next_name: "골드", to_next: 9 }), "골드까지 판매 9건 남았어요");
});

test("관리자는 포인트가 없어도 유료방에 들어갈 수 있다(입장료를 내지 않는다)", () => {
  const room = { is_member: false, is_open: true, expires_ms: Date.now() + 3600_000, member_count: 1, capacity: 10, entry_fee: 100 };
  const old = Date.now() - 10 * 86_400_000;
  assert.equal(canJoin(room, { points_balance: 0, created_ms: old }).ok, false);
  assert.equal(canJoin(room, { points_balance: 0, points_unlimited: true, created_ms: old }).ok, true);
});
