import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../src/pages/StudioPro.jsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/pages/StudioPro.css", import.meta.url), "utf8");
const appJsx = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const apiJs = readFileSync(new URL("../src/api.js", import.meta.url), "utf8");

test("라우트가 형제 화면(/builder) 아래에 등록돼 있다", () => {
  assert.match(appJsx, /path="\/builder\/pro"\s+element=\{<StudioPro \/>\}/);
  assert.doesNotMatch(appJsx, /\/studio\/pro/, "앱에 /studio 경로는 없다");
});

test("api 객체에 검증 · 해설 메서드가 있고 올바른 경로로 POST 한다", () => {
  assert.match(apiJs, /validate:\s*\(macro, windows\)[\s\S]{0,160}\/api\/validate"[\s\S]{0,120}method: "POST"/);
  assert.match(apiJs, /validateExplain:\s*\(macro, summary\)[\s\S]{0,160}\/api\/validate\/explain"[\s\S]{0,120}method: "POST"/);
  assert.doesNotMatch(apiJs, /\bpostValidate\b/, "api.js 에는 모듈 수준 post 가 없다");
});

test("폴백 라벨을 직접 쓰지 않고 공용 함수를 쓴다", () => {
  assert.match(page, /analysisLabel\(/);
  assert.doesNotMatch(page, /"AI 분석"/, "라벨 문자열을 화면에 박으면 폴백이 둔갑한다");
  assert.doesNotMatch(page, /AI 분석/, "주석이든 문구든 이 문자열을 화면 파일에 두지 않는다");
});

const indexCss = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");
const studioJsx = readFileSync(new URL("../src/pages/Studio.jsx", import.meta.url), "utf8");
const NAMED_COLORS = "white|black|red|green|blue|gray|grey|yellow|orange|purple|pink|brown|cyan|magenta|silver|gold|navy|teal|lime|maroon|olive|aqua|fuchsia|indigo|violet|crimson|coral|salmon|tomato";
const COLOR_PROPS = "color|background(?:-color)?|border(?:-[a-z]+)*|outline(?:-color)?|fill|stroke|box-shadow|text-shadow";

// 주석을 걷어 낸 코드만 본다 — 규칙 설명 주석이 규칙에 걸리지 않게.
const stripComments = (text) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

test("색은 토큰만 쓴다", () => {
  const code = stripComments(css);
  assert.doesNotMatch(code, /#[0-9a-fA-F]{3,8}\b/, "하드코딩 색 대신 --c-* 토큰");
  assert.doesNotMatch(code, /\.dark\b/, "다크 규칙을 따로 두지 않는다");
  assert.doesNotMatch(code, /\b(?:hsla?|hwb|lab|lch|oklab|oklch|color)\(/i, "rgb(var(--c-…)) 외의 색 함수 금지");
  assert.doesNotMatch(code, /\brgba?\((?!\s*var\(--c-)/i, "rgb 는 토큰을 감싸는 형태만");
  const named = new RegExp("(?:" + COLOR_PROPS + ")\\s*:[^;{}]*\\b(?:" + NAMED_COLORS + ")\\b", "i");
  assert.doesNotMatch(code.replace(/var\(--c-[a-z0-9-]+\)/g, "var()"), named, "색 이름 금지"); // 토큰 이름 속의 green · red 는 색 이름이 아니다
  assert.match(code, /rgb\(var\(--c-/);
  const pageCode = stripComments(page);
  assert.doesNotMatch(pageCode, /#[0-9a-fA-F]{3,8}\b/);
  assert.doesNotMatch(pageCode, /\b(?:rgba?|hsla?)\(/i, "화면 코드에 색 함수를 쓰지 않는다");
  assert.doesNotMatch(pageCode, /\bstyle=\{\{[^}]*(?:color|background)/i, "인라인 색을 쓰지 않는다");
});

test("쓰는 --c-* 토큰은 모두 index.css 에 정의돼 있다", () => {
  const used = [...new Set([...stripComments(css).matchAll(/var\((--c-[a-z0-9-]+)/g)].map((m) => m[1]))];
  assert.ok(used.length > 5, "토큰을 하나도 못 읽었다면 이 시험이 아무것도 보지 않는 것이다");
  const defined = new Set([...indexCss.matchAll(/(--c-[a-z0-9-]+)\s*:/g)].map((m) => m[1]));
  const missing = used.filter((name) => !defined.has(name));
  assert.deepEqual(missing, [], `정의되지 않은 토큰: ${missing.join(", ")}`);
});

test("실패한 구간 · 못 잰 값 · 근거 못 찾음을 공용 규칙으로 그린다", () => {
  assert.match(page, /barScale\(bars\)/);
  assert.match(page, /headlineNote\(/);
  assert.match(page, /metricText\(/);
});

test("검증 중 조건을 고친 결과는 지난 결과로 남는다", () => {
  assert.match(page, /formVersion/);
  assert.doesNotMatch(page, /setStale\(false\)/, "도착한 결과를 무조건 최신으로 두지 않는다");
});

test("두 검증 요청에는 시간 상한이 있다", () => {
  assert.match(apiJs, /"\/api\/validate",\s*\{\s*method: "POST", timeoutMs: \d/);
  assert.match(apiJs, /"\/api\/validate\/explain",\s*\{\s*method: "POST", timeoutMs: \d/);
});

test("기본 빌더에서 프로로 열 수 있고 지금 조건이 따라간다", () => {
  assert.match(studioJsx, /프로로 열기/);
  assert.match(studioJsx, /navigate\("\/builder\/pro", \{ state: \{ macro: currentMacro \} \}\)/);
  assert.match(page, /location\.state/);
  assert.match(page, /macroToForm\(/);
});

test("금지한 권유 표현을 화면 · 스타일에 쓰지 않는다", () => {
  assert.doesNotMatch(page, /추천/);
  assert.doesNotMatch(css, /추천/);
});

test("빌더 패널을 재사용하고 검증 요청 전에 입력 검증을 거친다", () => {
  assert.match(page, /import Builder from "\.\.\/components\/Builder\.jsx"/);
  assert.match(page, /<Builder form=\{form\} setForm=\{/);
  assert.match(page, /validateDetailed\(form\)/);
  assert.match(page, /buildMacro\(form\)/);
  assert.doesNotMatch(page, /useState\(null\);\s*\/\/ 빌더 패널이 채운다/);
  assert.doesNotMatch(page, /disabled=\{!macro/, "채워 주는 곳이 없는 상태에 버튼을 묶지 않는다");
});

test("실패한 구간은 pct 를 읽기 전에 거른다", () => {
  assert.doesNotMatch(page, /Math\.(max|min)\(\.\.\.[^)]*\.pct/, "null 이 0 으로 바뀌는 축 계산 금지");
  assert.match(page, /bar\.failed/);
});
