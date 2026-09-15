import test from "node:test";
import assert from "node:assert/strict";
import { visibleDotIndexes } from "../src/lib/carouselDots.js";

test("점이 적으면 모두, 많으면 현재 기사를 가운데 둔 창만", () => {
  assert.deepEqual(visibleDotIndexes(0, 5), [0, 1, 2, 3, 4]);
  assert.deepEqual(visibleDotIndexes(0, 73), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.deepEqual(visibleDotIndexes(40, 73), [35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45]);
  assert.deepEqual(visibleDotIndexes(72, 73), [62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72]);
  assert.deepEqual(visibleDotIndexes(-1, 73), [62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72], "음수 인덱스는 뒤에서 센다");
  assert.deepEqual(visibleDotIndexes(3, 20, 5), [1, 2, 3, 4, 5]);
  assert.deepEqual(visibleDotIndexes(0, 0), []);
});
