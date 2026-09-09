import test from "node:test";
import assert from "node:assert/strict";
import { boardFullTime, boardTime, initialOf, kstDateTime, pageWindow } from "../src/lib/boardText.js";

test("initialOf takes the first syllable or an uppercase letter, ? when empty", () => {
  assert.equal(initialOf("노희재"), "노");
  assert.equal(initialOf(" bd_ea3de26c"), "B");
  assert.equal(initialOf(""), "?");
  assert.equal(initialOf(null), "?");
});

test("kstDateTime turns the board's KST label into a datetime attribute", () => {
  assert.equal(kstDateTime("2026-09-04 15:59"), "2026-09-04T15:59:00+09:00");
  assert.equal(kstDateTime("어제"), "");
});

// 2026-09-09 13:00 KST = 2026-09-09T04:00Z
const NOW = Date.UTC(2026, 8, 9, 4, 0, 0);

test("boardTime shows HH:MM today, MM.DD this year, YY.MM.DD before that (all KST)", () => {
  assert.equal(boardTime(Date.UTC(2026, 8, 9, 0, 5, 0), NOW), "09:05");          // 오늘 09:05 KST
  assert.equal(boardTime(Date.UTC(2026, 8, 8, 14, 59, 0), NOW), "09.08");        // 어제 23:59 KST
  assert.equal(boardTime(Date.UTC(2026, 8, 8, 15, 0, 0), NOW), "00:00");         // 오늘 00:00 KST — KST 기준으로 가른다
  assert.equal(boardTime(Date.UTC(2026, 0, 3, 3, 0, 0), NOW), "01.03");          // 올해
  assert.equal(boardTime(Date.UTC(2025, 11, 31, 20, 0, 0), NOW), "01.01");       // UTC 로는 작년, KST 로는 올해 1월 1일 → 올해 형식
  assert.equal(boardTime(Date.UTC(2025, 11, 30, 3, 0, 0), NOW), "25.12.30");      // 작년
  assert.equal(boardTime(Date.UTC(2024, 4, 1, 3, 0, 0), NOW), "24.05.01");       // 그 전
  assert.equal(boardTime("x", NOW), "");
});

test("boardFullTime gives the detail label", () => {
  assert.equal(boardFullTime(Date.UTC(2026, 8, 4, 6, 59, 0)), "2026.09.04 15:59");
});

test("pageWindow keeps the current page centred and fills five at the edges", () => {
  assert.deepEqual(pageWindow(1, 10), [1, 2, 3, 4, 5]);
  assert.deepEqual(pageWindow(5, 10), [3, 4, 5, 6, 7]);
  assert.deepEqual(pageWindow(10, 10), [6, 7, 8, 9, 10]);
  assert.deepEqual(pageWindow(2, 3), [1, 2, 3]);
  assert.deepEqual(pageWindow(1, 0), []);
});
