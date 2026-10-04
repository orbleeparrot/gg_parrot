import assert from "node:assert/strict";
import { test } from "node:test";
import { barScale, headlineNote, metricText } from "../src/lib/validationFormat.js";
import { windowBars } from "../src/lib/validationView.js";

test("못 잰 값(null · undefined · 비유한)은 0 이 아니라 줄표로 보인다", () => {
  assert.equal(metricText(null), "—");
  assert.equal(metricText(undefined), "—");
  assert.equal(metricText(Number.NaN), "—");
  assert.equal(metricText("1.2"), "—");
  assert.equal(metricText(0), "0.00", "진짜 0 은 0 으로 보인다");
  assert.equal(metricText(1.234), "1.23");
  assert.equal(metricText(12.5, { suffix: "%" }), "12.50%");
});

test("막대 축은 실패한 구간을 빼고 잡는다", () => {
  const bars = windowBars([{ index: 1, return_pct: 10 }, { index: 2, return_pct: null }, { index: 3, return_pct: -25 }]);
  assert.equal(barScale(bars), 25);
  assert.equal(barScale(windowBars([{ index: 1, return_pct: null }])), 0, "전부 실패하면 축이 없다");
  assert.equal(barScale([]), 0);
});

test("막대 축은 null 때문에 줄어들지 않는다", () => {
  const bars = windowBars([{ index: 1, return_pct: -3 }, { index: 2, return_pct: null }]);
  assert.equal(barScale(bars), 3, "null 이 0 으로 읽히면 이 값이 0 이 된다");
});

test("헤드라인을 못 찾았을 때는 사유별로 '찾지 못했다' 고 말한다", () => {
  assert.match(headlineNote({ found: false, reason: "보존_범위_밖", items: [] }), /찾지 못했/);
  assert.match(headlineNote({ found: false, reason: "조회_실패", items: [] }), /찾지 못했/);
  assert.match(headlineNote({ found: false, reason: "", items: [] }), /찾지 못했/);
  assert.match(headlineNote(undefined), /찾지 못했/);
  assert.notEqual(headlineNote({ found: false, reason: "보존_범위_밖" }), headlineNote({ found: false, reason: "조회_실패" }));
  assert.doesNotMatch(headlineNote({ found: false, reason: "" }), /없었|없어요|없습니다/, "없다고 단정하지 않는다");
});

test("찾았으면 안내 문구를 만들지 않는다", () => {
  assert.equal(headlineNote({ found: true, reason: "", items: [{ title: "x" }] }), "");
});
