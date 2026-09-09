import test from "node:test";
import assert from "node:assert/strict";
import {
  formatPoints, fullKst, joinedLabel, ledgerLabel, reasonLabel, refEntryId, signedPoints, stampKst,
  tierNextLabel, tierStepAt, tierSteps, toMs,
} from "../src/lib/profileText.js";

// 2026-09-09 13:00 KST
const NOW = Date.UTC(2026, 8, 9, 4, 0, 0);

test("toMs reads ISO strings and numbers, null otherwise", () => {
  assert.equal(toMs("2026-08-12T05:00:00Z"), Date.UTC(2026, 7, 12, 5, 0, 0));
  assert.equal(toMs(1234), 1234);
  assert.equal(toMs("어제"), null);
  assert.equal(toMs(undefined), null);
});

test("stampKst shows MM.DD HH:MM this year and YY.MM.DD before, in KST", () => {
  assert.equal(stampKst("2026-08-12T05:00:00Z", NOW), "08.12 14:00");
  assert.equal(stampKst(Date.UTC(2025, 11, 31, 20, 0, 0), NOW), "01.01 05:00"); // KST 로는 올해 1월 1일
  assert.equal(stampKst("2025-03-02T01:00:00Z", NOW), "25.03.02");
  assert.equal(stampKst("x", NOW), "");
});

test("fullKst and joinedLabel", () => {
  assert.equal(fullKst("2026-08-12T05:00:00Z"), "2026.08.12 14:00");
  assert.equal(joinedLabel("2026-08-12T05:00:00Z"), "2026.08.12 가입");
  assert.equal(joinedLabel(null), "");
});

test("points formatting keeps the sign and the P suffix", () => {
  assert.equal(formatPoints(1240), "1,240P");
  assert.equal(formatPoints(undefined), "0P");
  assert.equal(signedPoints(70), "+70P");
  assert.equal(signedPoints(-100), "−100P");
  assert.equal(signedPoints(0), "0P");
});

test("ledger labels translate the reason and append the entry symbol from the ref", () => {
  assert.equal(reasonLabel("unlock_spend"), "매크로 언락");
  assert.equal(reasonLabel("mystery"), "mystery");
  assert.equal(refEntryId("entry:123"), 123);
  assert.equal(refEntryId(""), null);
  assert.equal(ledgerLabel({ reason: "unlock_earn", ref: "entry:7" }, { 7: "BTCUSDT" }), "판매 수익 · BTCUSDT");
  assert.equal(ledgerLabel({ reason: "signup_grant", ref: "" }, { 7: "BTCUSDT" }), "가입 보너스");
});

test("tierSteps marks done/current/todo from the ladder and sales count", () => {
  const tier = { name: "브론즈", sales: 3, next_name: "실버", to_next: 2,
    ladder: [{ name: "새싹", at: 0 }, { name: "브론즈", at: 1 }, { name: "실버", at: 5 }, { name: "골드", at: 15 }, { name: "다이아", at: 40 }] };
  assert.deepEqual(tierSteps(tier).map((s) => s.state), ["done", "current", "todo", "todo", "todo"]);
  assert.deepEqual(tierSteps({ sales: 0 }).map((s) => s.state), ["current", "todo", "todo", "todo", "todo"]); // 사다리 없는 옛 응답
  assert.deepEqual(tierSteps({ sales: 99, ladder: tier.ladder }).map((s) => s.state), ["done", "done", "done", "done", "current"]);
});

test("tier captions", () => {
  assert.equal(tierNextLabel({ next_name: "실버", to_next: 2 }), "실버까지 판매 2건 남았어요");
  assert.equal(tierNextLabel({ next_name: null }), "가장 높은 등급이에요");
  assert.equal(tierStepAt(0), "시작");
  assert.equal(tierStepAt(15), "판매 15건");
});
