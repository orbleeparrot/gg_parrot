import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const page = readFileSync(new URL("../src/pages/StudioPro.jsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/pages/StudioPro.css", import.meta.url), "utf8");
const appJsx = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
const apiJs = readFileSync(new URL("../src/api.js", import.meta.url), "utf8");

test("라우트가 형제 화면(/builder) 아래에 등록돼 있다", () => {
  // 2026-10-08: 프로 빌더를 지금 버전에서 멈추고 잠갔다. 길은 그대로 있고 깃발을 보고 갈라진다 —
  // 잠금 자체는 proBuilderLocked.test.js 가 본다. 이 파일은 StudioPro 가 성한지를 본다.
  assert.match(appJsx, /path="\/builder\/pro"\s+element=\{PRO_BUILDER_OPEN \? <StudioPro \/> : <ProBuilderClosed \/>\}/);
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
const modeMenu = readFileSync(new URL("../src/components/BuilderModeMenu.jsx", import.meta.url), "utf8");
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

test("지난 결과 표시는 setForm 호출 횟수가 아니라 조건의 값 비교로 낸다", () => {
  // 공용 조건 판은 값이 그대로인 갱신도 보낸다(펀딩비 적용 두 번, 거래소 전환 뒤 늦은 종목 복원).
  // 호출마다 '바뀜' 으로 치면 방금 나온 결과가 지난 결과로 둔갑한다 — 값 비교(sameForm)는 validationView 시험이 본다.
  const code = stripComments(page);
  assert.match(code, /sameForm\(form, reportForm\)/, "지금 조건과 결과를 만든 조건을 값으로 견준다");
  assert.match(code, /setReportForm\(startedWith\)/, "결과와 함께 그 결과를 만든 조건을 담는다");
  assert.match(code, /<Builder form=\{form\} setForm=\{setForm\}[^>]*\/>/, "setForm 을 감싸 호출마다 고침으로 세지 않는다");
  assert.doesNotMatch(code, /setForm=\{\s*\(/, "setForm 자리에 새 함수를 넣지 않는다");
  assert.doesNotMatch(code, /setStale\(|formVersion/, "별도 stale 상태나 호출 횟수 세기를 두지 않는다");
});

test("두 검증 요청에는 시간 상한이 있다", () => {
  assert.match(apiJs, /"\/api\/validate",\s*\{\s*method: "POST", timeoutMs: \d/);
  assert.match(apiJs, /"\/api\/validate\/explain",\s*\{\s*method: "POST", timeoutMs: \d/);
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

// 들어갈 길만 있고 나올 길이 없으면 프로 빌더는 막힌 방이다. 두 화면이 같은 메뉴를 쓰는지 본다.
test("빌더 전환은 양방향이고, 어느 쪽으로 가든 지금 조건을 들고 간다", () => {
  assert.match(modeMenu, /value: "basic", label: "기본 빌더", path: "\/builder"/);
  assert.match(modeMenu, /value: "pro", label: "프로 빌더", path: "\/builder\/pro"/);
  for (const [name, code] of [["기본", studioJsx], ["프로", page]]) {
    assert.match(code, /import BuilderModeMenu from "\.\.\/components\/BuilderModeMenu\.jsx"/, `${name} 빌더가 공용 메뉴를 쓴다`);
    assert.match(code, /<BuilderModeMenu/, `${name} 빌더가 메뉴를 그린다`);
    assert.match(code, /onSwitch=\{/, `${name} 빌더가 전환을 받는다`);
  }
  assert.match(studioJsx, /mode="basic"/);
  assert.match(page, /mode="pro"/);
  // 경로는 메뉴가 고른 것을 쓴다 — 화면마다 따로 적으면 한쪽만 고쳐져 어긋난다.
  assert.match(studioJsx, /navigate\(target\.path, \{ state: \{ macro: currentMacro, source: "builder-mode" \} \}\)/);
  assert.match(page, /navigate\(target\.path, macro \? \{ state: \{ macro, source: "builder-mode" \} \} : undefined\)/);
  assert.match(page, /seedForm\(location\.state\)/);
  assert.doesNotMatch(studioJsx, /function BuilderModeMenu/, "지역 사본을 두지 않는다");
});

// 조건만 있고 차트가 없으면 무엇을 만드는지 볼 수 없다 — 기본 빌더와 같은 조각, 같은 연결.
test("프로 빌더에 차트가 있고 조건을 그대로 따라온다", () => {
  const code = stripComments(page);
  assert.match(code, /import CandleChart from "\.\.\/components\/CandleChart\.jsx"/);
  assert.match(code, /<CandleChart/);
  assert.match(code, /variant="studio"/);
  // 종목 · 봉 간격 · 시장 · 보조지표가 모두 지금 조건에서 나온다.
  assert.match(code, /symbol=\{symbol\}/);
  assert.match(code, /exchange=\{form\.exchange \|\| "binance"\}/);
  assert.match(code, /market=\{chartMarket\}/);
  assert.match(code, /interval=\{form\.candle_interval/);
  assert.match(code, /overlay=\{overlay\}/);
  assert.match(code, /computeStrategyOverlay\(form, candles\)/);
  // 차트 도구줄에서 봉 간격을 바꾸면 조건도 함께 바뀐다(한 곳만 고친다).
  assert.match(code, /onIntervalChange=\{\(value\) => setForm\(\(previous\) => \(\{ \.\.\.previous, candle_interval: value \}\)\)\}/);
  // 국내 현물에는 선물 봉이 없다.
  assert.match(code, /isDomestic\(form\.exchange\)\s*\?\s*"spot"/);
  // 종목이 없을 때 빈 판을 그린다 — 차트 자리가 소리 없이 사라지지 않게.
  assert.match(code, /chartSymbols\.length > 0 \?/);
  assert.match(code, /<EmptyState/);
  // 높이를 정해 주지 않으면 studio 차트는 0px 로 접힌다.
  assert.match(stripComments(css), /\.pro-chart-body \{[^}]*height:/);
});

// 빌더끼리 오간 조건은 어느 출처도 아니다 — 리더보드 복사로 적으면 배지가 거짓말을 한다.
test("빌더 전환으로 들어온 조건에 출처 배지를 붙이지 않는다", () => {
  assert.match(studioJsx, /source === "builder-mode"\s*\n?\s*\?\s*null/);
});
