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

test("색은 토큰만 쓴다", () => {
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/, "하드코딩 색 대신 --c-* 토큰");
  assert.doesNotMatch(css, /\.dark\b/, "다크 규칙을 따로 두지 않는다");
  assert.doesNotMatch(page, /#[0-9a-fA-F]{3,8}\b/);
  assert.doesNotMatch(page, /\bstyle=\{\{[^}]*(rgb|hsl|#)/, "인라인 색을 쓰지 않는다");
  assert.match(css, /rgb\(var\(--c-/);
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
