import { test } from "node:test";
import assert from "node:assert/strict";
import { endLabel, environmentLabel, runDurationLabel, runPeriodLabel } from "../src/features/agents/history.js";

test("실행 길이는 초·분·시간·일 단위로 읽기 쉽게", () => {
  assert.equal(runDurationLabel("2026-09-29T23:34:20Z", "2026-09-29T23:34:25Z"), "5초");
  assert.equal(runDurationLabel("2026-09-29T23:44:20Z", "2026-09-30T00:05:31Z"), "21분");
  assert.equal(runDurationLabel("2026-09-29T00:00:00Z", "2026-09-29T03:12:00Z"), "3시간 12분");
  assert.equal(runDurationLabel("2026-09-29T00:00:00Z", "2026-10-01T04:00:00Z"), "2일 4시간");
  assert.equal(runDurationLabel("", "2026-10-01T04:00:00Z"), "");
});

test("날짜 범위는 한국 시간, 같은 날이면 끝 시각만", () => {
  // 23:44Z = 다음 날 08:44 KST
  assert.equal(runPeriodLabel("2026-09-29T23:44:20Z", "2026-09-30T00:05:31Z"), "9월 30일 (수) 08:44 – 09:05");
  assert.equal(runPeriodLabel("2026-09-29T14:40:00Z", "2026-09-29T16:10:00Z"), "9월 29일 (화) 23:40 – 9월 30일 01:10");
});

test("종료 방식 — 오류만 주의 톤", () => {
  assert.deepEqual(endLabel({ status: "error" }), { text: "실행 오류", tone: "warn" });
  assert.deepEqual(endLabel({ status: "stopped", stop_mode: "close_and_stop" }), { text: "청산 후 종료", tone: "muted" });
  assert.deepEqual(endLabel({ status: "stopped", note: "포지션 없이 종료" }), { text: "포지션 없이 종료", tone: "muted" });
  assert.equal(environmentLabel({ testnet: false, market: "futures", leverage: 3 }), "실거래 · 선물 3배");
});
