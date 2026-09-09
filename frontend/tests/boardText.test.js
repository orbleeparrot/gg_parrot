import test from "node:test";
import assert from "node:assert/strict";
import { initialOf, kstDateTime, pageWindow } from "../src/lib/boardText.js";

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

test("pageWindow keeps the current page centred and fills five at the edges", () => {
  assert.deepEqual(pageWindow(1, 10), [1, 2, 3, 4, 5]);
  assert.deepEqual(pageWindow(5, 10), [3, 4, 5, 6, 7]);
  assert.deepEqual(pageWindow(10, 10), [6, 7, 8, 9, 10]);
  assert.deepEqual(pageWindow(2, 3), [1, 2, 3]);
  assert.deepEqual(pageWindow(1, 0), []);
});
