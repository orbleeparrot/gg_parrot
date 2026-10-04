import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  analysisLabel, KNOWN_WARNING_CODES, sameForm, UNKNOWN_WARNING_TEXT, warningText, windowBars,
} from "../src/lib/validationView.js";

test("경고 코드 네 개는 모두 사람이 읽는 문장이 된다", () => {
  assert.match(warningText("한_구간_집중"), /한 구간/);
  assert.match(warningText("표본_부족"), /거래 수/);
  assert.match(warningText("후반부_음수"), /음수/);
  assert.match(warningText("거래_집중"), /거래/);
});

test("모르는 경고 코드는 식별자 대신 일반 문장이 된다 — 화면에서 말없이 사라지지 않는다", () => {
  assert.equal(UNKNOWN_WARNING_TEXT, "서버가 새 경고를 표시했어요");
  for (const code of ["없는_코드", "", undefined, null, 42, "toString", "__proto__"]) {
    assert.equal(warningText(code), UNKNOWN_WARNING_TEXT);
  }
});

test("화면이 아는 경고 코드는 백엔드 WARNING_CODES 와 같은 목록이다", () => {
  const source = readFileSync(new URL("../../backend/app/engine/validation.py", import.meta.url), "utf8");
  const match = source.match(/^WARNING_CODES\s*=\s*\(([^)]*)\)/m);
  assert.ok(match, "백엔드 WARNING_CODES 를 못 읽었다 — 이 시험이 아무것도 보지 않는 것이다");
  const backend = [...match[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]);
  assert.ok(backend.length >= 4);
  assert.deepEqual([...KNOWN_WARNING_CODES].sort(), [...backend].sort(),
    "백엔드가 코드를 더하거나 바꾸면 화면 문장도 같이 고쳐야 한다");
  for (const code of backend) assert.notEqual(warningText(code), UNKNOWN_WARNING_TEXT, `${code} 문장이 없다`);
});

test("sameForm 은 같은 내용이면 객체가 달라도 같다고 본다", () => {
  const base = { symbol: "BTCUSDT", funding_pct: 0.01, params: { a: [1, 2] }, symbols: null };
  assert.ok(sameForm(base, base));
  assert.ok(sameForm(base, { ...base, params: { a: [1, 2] } }), "펀딩비 적용을 두 번 눌러 같은 값이 되는 경우");
  assert.ok(sameForm({ x: NaN }, { x: NaN }));
});

test("sameForm 은 값이 하나라도 다르면 다르다고 본다", () => {
  const base = { symbol: "BTCUSDT", funding_pct: 0.01, params: { a: [1, 2] } };
  assert.ok(!sameForm(base, { ...base, funding_pct: 0.02 }));
  assert.ok(!sameForm(base, { ...base, params: { a: [1, 3] } }));
  assert.ok(!sameForm(base, { ...base, extra: 1 }));
  assert.ok(!sameForm({ a: undefined }, { b: undefined }), "키가 다르면 값이 둘 다 undefined 여도 다르다");
  assert.ok(!sameForm({ a: [] }, { a: {} }));
  assert.ok(!sameForm(null, {}));
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
  assert.equal(warningText(), UNKNOWN_WARNING_TEXT);
  assert.equal(analysisLabel(), "자동 요약");
});

test("Infinity 와 -Infinity 는 막대 길이가 될 수 없어 실패로 남는다", () => {
  const bars = windowBars([
    { index: 1, return_pct: Infinity },
    { index: 2, return_pct: -Infinity },
  ]);
  assert.deepEqual(bars[0], { index: 1, pct: null, failed: true });
  assert.deepEqual(bars[1], { index: 2, pct: null, failed: true });
});

test("숫자처럼 생긴 문자열은 숫자로 받아들이지 않는다", () => {
  const [bar] = windowBars([{ index: 1, return_pct: "12.5" }]);
  assert.deepEqual(bar, { index: 1, pct: null, failed: true });
});

test("-0 은 실패가 아니라 값이고 -0 그대로 넘어간다", () => {
  const [bar] = windowBars([{ index: 1, return_pct: -0 }]);
  assert.equal(bar.failed, false);
  assert.ok(Object.is(bar.pct, -0));
});

test("문자열처럼 행동하는 값은 'ai' 로 쳐 주지 않는다", () => {
  assert.equal(analysisLabel(new String("ai")), "자동 요약");
  assert.equal(analysisLabel({ toString() { return "ai"; } }), "자동 요약");
  assert.equal(analysisLabel({ valueOf() { return "ai"; } }), "자동 요약");
  assert.equal(analysisLabel(["ai"]), "자동 요약");
});

test("경고 문장은 코드가 아니라 서로 다른 한국어 문장이다", () => {
  const codes = ["한_구간_집중", "표본_부족", "후반부_음수", "거래_집중"];
  const texts = codes.map(warningText);
  for (const [i, text] of texts.entries()) {
    assert.notEqual(text, "");
    assert.notEqual(text, codes[i]);
    assert.ok(!text.includes("_"), `${codes[i]} 문장에 식별자 흔적이 있다`);
    assert.match(text, /요$/);
  }
  assert.equal(new Set(texts).size, codes.length);
});
