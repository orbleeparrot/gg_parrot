import assert from "node:assert/strict";
import test from "node:test";

import { canJoin, joinConfirmText, remainingLabel, roomTabs, validateCreate } from "../src/lib/roomFlow.js";
import { chatScope } from "../src/lib/chatBadge.js";

const DAY = 24 * 3600 * 1000;

test("remainingLabel shows days, then hours, then '곧 종료', then '끝남'", () => {
  const now = 1_000_000_000_000;
  assert.equal(remainingLabel(now + 5 * DAY + 3600 * 1000, now), "5일 남음");
  assert.equal(remainingLabel(now + 5 * 3600 * 1000, now), "5시간 남음");
  assert.equal(remainingLabel(now + 30 * 60 * 1000, now), "곧 종료");
  assert.equal(remainingLabel(now - 1, now), "끝남");
});

test("canJoin explains each block", () => {
  const now = 1_000_000_000_000;
  const me = { id: 7, points_balance: 500, created_at: new Date(now - 10 * DAY).toISOString() };
  const room = { id: 1, is_open: true, is_member: false, member_count: 2, capacity: 3, entry_fee: 100, expires_ms: now + DAY };
  assert.deepEqual(canJoin(room, me, now), { ok: true, reason: "" });
  assert.equal(canJoin({ ...room, is_member: true }, me, now).reason, "이미 들어와 있어요");
  assert.equal(canJoin({ ...room, member_count: 3 }, me, now).reason, "정원이 다 찼어요");
  assert.equal(canJoin({ ...room, is_open: false }, me, now).reason, "끝난 방이에요");
  assert.equal(canJoin(room, { ...me, points_balance: 99 }, now).reason, "포인트가 부족해요");
  const young = { ...me, created_at: new Date(now - DAY).toISOString() };
  assert.equal(canJoin(room, young, now).reason, "가입 3일 후부터 유료방에 들어갈 수 있어요");
  assert.equal(canJoin({ ...room, entry_fee: 0 }, young, now).ok, true);
  assert.equal(canJoin(room, null, now).reason, "로그인이 필요해요");
});

test("joinConfirmText names the fee and says no refund", () => {
  assert.match(joinConfirmText({ entry_fee: 100, title: "비트 토론" }), /100포인트/);
  assert.match(joinConfirmText({ entry_fee: 100, title: "비트 토론" }), /환불/);
  assert.equal(joinConfirmText({ entry_fee: 0, title: "무료" }).includes("포인트"), false);
});

test("validateCreate enforces title 2~30, capacity 2~10, fee 0~300", () => {
  assert.equal(validateCreate({ title: "비트 토론", capacity: 5, entryFee: 100 }).ok, true);
  assert.equal(validateCreate({ title: "a", capacity: 5, entryFee: 0 }).errors.title, "2~30자로 적어 주세요");
  assert.equal(validateCreate({ title: "x".repeat(31), capacity: 5, entryFee: 0 }).errors.title, "2~30자로 적어 주세요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 1, entryFee: 0 }).errors.capacity, "2~10명이에요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 11, entryFee: 0 }).errors.capacity, "2~10명이에요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 5, entryFee: 301 }).errors.entryFee, "0~300포인트예요");
  assert.equal(validateCreate({ title: "좋은 방", capacity: 5, entryFee: -1 }).errors.entryFee, "0~300포인트예요");
});

test("roomTabs puts 전체 first, my rooms (open first, active pinned), 방 찾기 last", () => {
  const mine = [
    { id: 1, title: "A", is_open: true, last_seen_id: 0 },
    { id: 2, title: "B", is_open: false, last_seen_id: 0 },
    { id: 3, title: "C", is_open: true, last_seen_id: 0 },
  ];
  const tabs = roomTabs(mine, 3);
  assert.deepEqual(tabs.map((t) => t.key), ["all", "room:3", "room:1", "room:2", "find"]);
  assert.equal(tabs[1].label, "C");
  assert.equal(tabs[3].ended, true);
  assert.deepEqual(roomTabs([], null).map((t) => t.key), ["all", "find"]);
});

test("chatScope keeps legacy scopes and adds a room suffix", () => {
  assert.equal(chatScope(null), "anon");
  assert.equal(chatScope(5), "member:5");
  assert.equal(chatScope(5, 0), "member:5");
  assert.equal(chatScope(5, 12), "member:5:room:12");
});
