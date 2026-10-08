import assert from "node:assert/strict";
import test from "node:test";
import { BOARD_INKS, inkKeyOf } from "../src/lib/boardInk.js";
import { heatLevel } from "../src/lib/heat.js";

test("편집기 팔레트 색은 hex·rgb 어느 표기든 같은 이름으로 읽는다", () => {
  for (const ink of BOARD_INKS) assert.equal(inkKeyOf(ink.hex), ink.key);
  assert.equal(inkKeyOf("#F6465D"), "red");
  assert.equal(inkKeyOf("rgb(246, 70, 93)"), "red");
  assert.equal(inkKeyOf("rgb(246 70 93)"), "red");
  assert.equal(inkKeyOf("rgba(14, 203, 129, 1)"), "green");
});

test("팔레트 밖의 색·반투명 색은 버린다 — 다크 화면에서 복사해 온 글자색(GG-001)", () => {
  assert.equal(inkKeyOf("rgb(221, 225, 231)"), null);
  assert.equal(inkKeyOf("#dde1e7"), null);
  assert.equal(inkKeyOf("rgba(246, 70, 93, 0.5)"), null);
  assert.equal(inkKeyOf(""), null);
  assert.equal(inkKeyOf("red"), null);
});

test("열 지도 값은 -1…+1, 본전과 계산 불가는 0(무채색)", () => {
  assert.equal(heatLevel(0, 10), 0);
  assert.equal(heatLevel(5, 10), 0.5);
  assert.equal(heatLevel(-30, 10), -1);
  assert.equal(heatLevel(3, 0), 0);
  assert.equal(heatLevel(Number.NaN, 10), 0);
});
