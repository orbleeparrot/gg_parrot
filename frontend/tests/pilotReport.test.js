import { test } from "node:test";
import assert from "node:assert/strict";
import {
  pilotAmount, pilotDuration, pilotLeftOut, pilotPct, pilotPeriod, pilotSpan, pilotTone, pilotWon, pilotWorstShare,
} from "../src/lib/pilotReport.js";

test("금액은 부호를 늘 붙이고 반올림해 0 이면 부호 없이", () => {
  assert.equal(pilotAmount(706.4), "+706");
  assert.equal(pilotAmount(-9310.2), "−9,310");
  assert.equal(pilotAmount(0.3), "0");
  assert.equal(pilotAmount(null), "—");
  assert.equal(pilotWon(-13529), "−13,529원");
  assert.equal(pilotWon(2706, { sign: false }), "2,706원");
  assert.equal(pilotTone(0.4), "is-zero");
  assert.equal(pilotTone(-1), "is-down");
  assert.equal(pilotPct(-2.071), "−2.07%");
  assert.equal(pilotPct(null), "—");
});

test("기간은 KST — 같은 날이면 끝 시각만, 실행 중이면 '지금'", () => {
  assert.equal(pilotSpan("2026-10-14T22:51:00Z", "2026-10-15T06:55:00Z", false), "10.15 07:51 – 15:55");
  assert.equal(pilotSpan("2026-10-14T22:51:00Z", "2026-10-15T22:44:00Z", false), "10.15 07:51 – 10.16 07:44");
  assert.equal(pilotSpan("2026-10-14T22:51:00Z", null, true), "10.15 07:51 – 지금");
  assert.equal(pilotDuration("2026-10-14T00:00:00Z", "2026-10-14T00:27:00Z"), "27분");
  assert.equal(pilotDuration("2026-10-14T00:00:00Z", "2026-10-14T08:06:00Z"), "8.1시간");
  assert.equal(pilotPeriod("2026-10-13T23:00:00Z", "2026-10-16T08:00:00Z"), "2026.10.14 08:00 – 10.16 17:00 KST");
});

test("손실 몫은 체결한 손실 계정끼리만 나눈다", () => {
  const accounts = [
    { name: "a", net: 706, traded_sessions: 1 },
    { name: "b", net: -1000, traded_sessions: 2 },
    { name: "c", net: -3000, traded_sessions: 1 },
    { name: "d", net: -50, traded_sessions: 0 },
  ];
  assert.deepEqual(pilotWorstShare(accounts), { name: "c", net: -3000, share: 75 });
  assert.equal(pilotWorstShare([{ name: "a", net: 1, traded_sessions: 1 }]), null);
  assert.equal(pilotLeftOut({ idle_sessions: 2, mock_sessions: 1 }), "목록에서 뺀 것: 체결 없이 끝난 실전 세션 2개, 모의 세션 1개.");
  assert.equal(pilotLeftOut({ idle_sessions: 0, mock_sessions: 0 }), "");
});
