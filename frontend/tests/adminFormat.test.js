import test from "node:test";
import assert from "node:assert/strict";
import {
  CHANNEL_DETAIL, DASH, EMPTY_NOTE, fmtDateTick, fmtDayTimeKst, fmtDuration, fmtInt, fmtKst, fmtLimit, fmtMonthLabel, fmtNum,
  fmtPct, fmtRelative, fmtShortDay, fmtSignedPct, fmtStamp, fmtTimeKst, fmtTokens, fmtUntil, fmtUsd, isAllZero, labelOf,
  meanBy, ratioPct, sumBy, sumOrNull, weightedMean,
} from "../src/lib/adminFormat.js";

test("missing values render as a dash, never as 0", () => {
  for (const fn of [fmtInt, fmtNum, fmtPct, fmtSignedPct, fmtUsd, fmtTokens, fmtDuration, fmtRelative, fmtUntil, fmtTimeKst, fmtDayTimeKst]) {
    assert.equal(fn(null), DASH, fn.name);
    assert.equal(fn(undefined), DASH, fn.name);
    assert.equal(fn("abc"), DASH, fn.name);
  }
  assert.equal(fmtKst("not a date"), DASH);
});

test("numbers: thousands separators, fixed percent digits, signed returns", () => {
  assert.equal(fmtInt(2486), "2,486");
  assert.equal(fmtInt("12.6"), "13");
  assert.equal(fmtNum(1.61, 2), "1.61");
  assert.equal(fmtNum(11, 1), "11");
  assert.equal(fmtPct(39.44), "39.4%");
  assert.equal(fmtPct(0), "0.0%");
  assert.equal(fmtPct(31, 0), "31%");
  assert.equal(fmtSignedPct(12.4), "+12.4%");
  assert.equal(fmtSignedPct(-2.3), "-2.3%");
  assert.equal(fmtSignedPct(0), "0.0%");
  assert.equal(fmtUsd(46.456), "$46.46");
  assert.equal(fmtUsd(0), "$0.00");
  assert.equal(fmtUsd(1234.5), "$1,234.50");
});

test("tokens shrink to M/K", () => {
  assert.equal(fmtTokens(38_200_000), "38.2M");
  assert.equal(fmtTokens(1_100_000), "1.10M");
  assert.equal(fmtTokens(412_000), "412.0K");
  assert.equal(fmtTokens(950), "950");
  assert.equal(fmtTokens(0), "0");
});

test("durations pad seconds and switch to hours", () => {
  assert.equal(fmtDuration(48), "0분 48초");
  assert.equal(fmtDuration(313), "5분 13초");
  assert.equal(fmtDuration(242), "4분 02초");
  assert.equal(fmtDuration(3720), "1시간 2분");
  assert.equal(fmtDuration(-5), "0분 00초");
});

test("relative times: 0 is a dash (never-run engine), recent is 지금", () => {
  const now = 10_000_000;
  assert.equal(fmtRelative(0, now), DASH);
  assert.equal(fmtRelative(now - 10_000, now), "지금");
  assert.equal(fmtRelative(now - 60_000, now), "1분 전");
  assert.equal(fmtRelative(now - 41 * 60_000, now), "41분 전");
  assert.equal(fmtRelative(now - 6 * 3_600_000, now), "6시간 전");
  assert.equal(fmtRelative(now - 3 * 86_400_000, now), "3일 전");
  assert.equal(fmtUntil(now + 42_000, now), "42초 뒤");
  assert.equal(fmtUntil(now + 16 * 60_000, now), "16분 뒤");
  assert.equal(fmtUntil(now - 1, now), "지금");
});

test("KST formatting converts UTC ISO stamps", () => {
  assert.equal(fmtKst("2026-09-17T01:42:00Z"), "2026-09-17 10:42");
  assert.equal(fmtKst(Date.UTC(2026, 8, 16, 15, 0)), "2026-09-17 00:00");
  assert.equal(fmtTimeKst(Date.UTC(2026, 8, 17, 1, 41)), "10:41");
  assert.equal(fmtDayTimeKst(Date.UTC(2026, 8, 16, 13, 10)), "09-16 22:10");
  assert.equal(fmtStamp("2026-09-17T01:42:00Z"), "2026-09-17 10:42 KST · 1분마다 갱신");
  assert.equal(fmtStamp(""), "갱신 시각 없음 · 1분마다 갱신");
});

test("day and month labels", () => {
  assert.equal(fmtShortDay("2026-09-01"), "09-01");
  assert.equal(fmtDateTick("2026-09-01"), "9/1");
  assert.equal(fmtDateTick("2026-12-25"), "12/25");
  assert.equal(fmtMonthLabel("2026-09"), "9월");
  assert.equal(fmtMonthLabel("2026-09", { current: true }), "9월*");
  assert.equal(fmtLimit(1000), "1,000");
  assert.equal(fmtLimit("설정값"), "설정값");
  assert.equal(fmtLimit(null), "없음");
});

test("aggregation helpers: sums, weighted means, ratios with empty denominators", () => {
  const rows = [
    { sessions: 100, bounce_pct: 40, avg: 60 }, { sessions: 300, bounce_pct: 20, avg: 120 }, { sessions: 0, bounce_pct: 99 },
  ];
  assert.equal(sumBy(rows, "sessions"), 400);
  assert.equal(weightedMean(rows, "bounce_pct", "sessions"), 25);
  assert.equal(weightedMean([], "bounce_pct", "sessions"), null);
  assert.equal(weightedMean([{ sessions: 0, bounce_pct: 10 }], "bounce_pct", "sessions"), null);
  assert.equal(ratioPct(3, 12), 25);
  assert.equal(ratioPct(3, 0), null);
  assert.equal(ratioPct(null, 5), null);
  assert.equal(isAllZero([0, 0, null]), true);
  assert.equal(isAllZero([]), true);
  assert.equal(isAllZero([0, 1]), false);
});

test("labels prefer the server label, then the map, then the raw code", () => {
  assert.equal(labelOf(CHANNEL_DETAIL, "search"), "검색 (google · naver · bing)");
  assert.equal(labelOf(CHANNEL_DETAIL, "search", "서버 라벨"), "서버 라벨");
  assert.equal(labelOf(CHANNEL_DETAIL, "unknown_code"), "unknown_code");
  assert.equal(labelOf(CHANNEL_DETAIL, null), DASH);
  assert.match(EMPTY_NOTE, /집계 시작 2026-09-17/);
});

test("sinceNote prints the coverage date and never invents one when the table is empty", async () => {
  const { DEVICE_LABELS, ENGINE_STATUS, METHOD_LABELS, sinceNote } = await import("../src/lib/adminFormat.js");
  assert.equal(sinceNote("2026-09-17"), "집계 시작 2026-09-17");
  assert.equal(sinceNote("2026-09-17", "방문 기록"), "방문 기록 2026-09-17");
  assert.equal(sinceNote(null), "집계 시작 전 (기록 없음)");
  assert.equal(sinceNote(""), "집계 시작 전 (기록 없음)");
  assert.equal(sinceNote("nope"), "집계 시작 전 (기록 없음)");
  assert.equal(DEVICE_LABELS.unknown, "알 수 없음");
  assert.equal(METHOD_LABELS.unknown, "알 수 없음");
  assert.equal(ENGINE_STATUS.skipped.tone, "off");
  assert.equal(ENGINE_STATUS.degraded.tone, "bad");
  assert.equal(ENGINE_STATUS.source_unavailable.label, "소스 실패");
});

test("sumOrNull: an all-null column sums to null (a dash), a real 0 stays 0", () => {
  // 집계 시작 전 날짜만 있는 창 — sumBy 는 0 을 돌려줘 합계 행에 "0" 이 찍혔다(가짜 숫자).
  assert.equal(sumOrNull([{ a: null }, { a: undefined }, {}], "a"), null);
  assert.equal(sumOrNull([], "a"), null);
  assert.equal(sumOrNull(null, "a"), null);
  assert.equal(sumOrNull([{ a: null }, { a: 3 }, { a: "4" }], "a"), 7, "null rows are skipped, numeric strings count");
  assert.equal(sumOrNull([{ a: 0 }, { a: null }], "a"), 0, "a measured 0 is a value, not an absence");
  assert.equal(sumOrNull([{ a: "abc" }], "a"), null);
  // 같은 입력에서 sumBy 는 0 — 두 함수의 차이가 곧 이 테스트의 이유.
  assert.equal(sumBy([{ a: null }], "a"), 0);
  assert.equal(fmtInt(sumOrNull([{ a: null }], "a")), DASH);
});

test("meanBy: simple mean over rows that have a value, null when none", () => {
  // 평균 세션 시간의 합계 행 — 측정된 세션 수를 서버가 주지 않아 세션 수 가중은 가짜 가중. 값이 있는 날의 단순 평균.
  assert.equal(meanBy([{ s: 60 }, { s: null }, { s: 120 }], "s"), 90);
  assert.equal(meanBy([{ s: 0 }, { s: 30 }], "s"), 15, "a measured 0 counts");
  assert.equal(meanBy([{ s: null }, {}], "s"), null);
  assert.equal(meanBy([], "s"), null);
  assert.equal(meanBy(undefined, "s"), null);
  assert.equal(fmtDuration(meanBy([{ s: null }], "s")), DASH);
});
