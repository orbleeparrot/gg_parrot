import test from "node:test";
import assert from "node:assert/strict";
import { analysisLabel, warningText, windowBars } from "../src/lib/validationView.js";

test("경고 코드 네 개는 모두 사람이 읽는 문장이 된다", () => {
  assert.match(warningText("한_구간_집중"), /한 구간/);
  assert.match(warningText("표본_부족"), /거래 수/);
  assert.match(warningText("후반부_음수"), /음수/);
  assert.match(warningText("거래_집중"), /거래/);
});

test("모르는 경고 코드는 식별자 대신 빈 문자열이 된다", () => {
  for (const code of ["없는_코드", "", undefined, null, 42, "toString", "__proto__"]) {
    assert.equal(warningText(code), "");
  }
});

test("실패한 구간은 막대 대신 표시로 남고 값은 null 이다", () => {
  const bars = windowBars([
    { index: 1, return_pct: 12.0, error: "" },
    { index: 2, return_pct: null, error: "ValueError" },
    { index: 3, return_pct: -4.5, error: "" },
  ]);
  assert.equal(bars.length, 3);
  assert.deepEqual(bars[0], { index: 1, pct: 12, failed: false });
  assert.deepEqual(bars[1], { index: 2, pct: null, failed: true });
  assert.deepEqual(bars[2], { index: 3, pct: -4.5, failed: false });
});

test("수익률 0 은 실패가 아니라 본전이다", () => {
  const [bar] = windowBars([{ index: 1, return_pct: 0, error: "" }]);
  assert.deepEqual(bar, { index: 1, pct: 0, failed: false });
});

test("return_pct 키가 없거나 값이 이상한 행도 실패로 남고 자리를 지킨다", () => {
  const bars = windowBars([{ index: 1 }, {}, null, { index: 4, return_pct: "x" }, { index: 5, return_pct: NaN }]);
  assert.equal(bars.length, 5);
  assert.deepEqual(bars[0], { index: 1, pct: null, failed: true });
  assert.deepEqual(bars[1], { index: 2, pct: null, failed: true });
  assert.deepEqual(bars[2], { index: 3, pct: null, failed: true });
  assert.deepEqual(bars[3], { index: 4, pct: null, failed: true });
  assert.deepEqual(bars[4], { index: 5, pct: null, failed: true });
});

test("AI 분석 라벨은 정확히 'ai' 일 때만 붙는다", () => {
  assert.equal(analysisLabel("ai"), "AI 분석");
  for (const source of ["fallback", "", undefined, null, "AI", "ai ", "Ai", "gpt", 1, {}]) {
    assert.equal(analysisLabel(source), "자동 요약");
  }
});

test("빈 입력에도 터지지 않는다", () => {
  assert.deepEqual(windowBars(null), []);
  assert.deepEqual(windowBars(undefined), []);
  assert.deepEqual(windowBars([]), []);
  assert.deepEqual(windowBars("nope"), []);
  assert.equal(warningText(), "");
  assert.equal(analysisLabel(), "자동 요약");
});
