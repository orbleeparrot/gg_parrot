import assert from "node:assert/strict";
import test from "node:test";
import { currentAllow, expectedAllow, HOOK_CLASSES, definedClasses } from "../scripts/lint-css-classes.mjs";

test("no-unknown-classes 허용 목록은 CSS 에 정의된 클래스와 같다 (npm run lint:classes)", () => {
  assert.deepEqual(currentAllow(), expectedAllow());
});

test("이름표 클래스에 CSS 가 생기면 목록에서 뺀다", () => {
  const defined = definedClasses();
  assert.deepEqual(HOOK_CLASSES.filter((name) => defined.has(name)), []);
});
