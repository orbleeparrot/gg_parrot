import test from "node:test";
import assert from "node:assert/strict";
import { paperMainButton } from "../src/lib/paperMain.js";

test("세션이 없으면 '시작'(노랑)", () => {
  assert.deepEqual(paperMainButton({ running: false, hasSession: false, busy: false }), { label: "시작", action: "start", tone: "primary" });
  assert.equal(paperMainButton({ running: false, hasSession: false, busy: true }).label, "시작 중…");
});

test("돌고 있으면 '중지'(빨강)", () => {
  assert.deepEqual(paperMainButton({ running: true, hasSession: true, busy: false }), { label: "중지", action: "stop", tone: "danger" });
  assert.equal(paperMainButton({ running: true, hasSession: true, busy: true }).label, "중지 중…");
});

test("중지된 세션이 있으면 '다시 시작'(노랑)", () => {
  assert.deepEqual(paperMainButton({ running: false, hasSession: true, busy: false }), { label: "다시 시작", action: "start", tone: "primary" });
});
