// 프로 빌더 두 열 배치 — 코치 패널이 조건 판과 **같은 화면에** 끼워졌는지 본다.
// 그려낸 글만 보면 "기능이 반대 화면에 들어간" 것을 놓친다(하드코딩한 props 로 그린 시험이 그랬다).
// 그래서 실제 소스도 함께 읽어 단정한다 — <CoachPanel …/> 이 StudioPro 안에 있는지, 프로 판 변형이 그대로인지.
import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { renderComponent, textOf } from "./renderHelper.js";

const SOURCE = readFileSync(new URL("../src/pages/StudioPro.jsx", import.meta.url), "utf8");

// jsx 파일에서 함수 하나만 꺼내 쓰기 위한 묶기 — 그리기와 달리 React 가 필요 없다.
async function loadPage(path) {
  const result = await build({
    entryPoints: [new URL(path, import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1")],
    bundle: true, write: false, format: "esm", platform: "node", jsx: "automatic",
    loader: { ".js": "jsx", ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
    define: { "process.env.NODE_ENV": '"production"' },
    banner: { js: 'import { createRequire } from "node:module"; const require = createRequire(import.meta.url);' },
    logLevel: "silent",
  });
  const file = join(mkdtempSync(join(tmpdir(), "ggp-page-")), "bundle.mjs");
  writeFileSync(file, result.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

test("프로 빌더에 코치 패널이 있다", async () => {
  const text = textOf(await renderComponent("src/pages/StudioPro.jsx", {}, { router: true }));
  assert.match(text, /껄무새 코치/);
  assert.match(text, /시작하기/);
});

test("코치 패널이 조건 판과 함께 그려진다", async () => {
  const html = await renderComponent("src/pages/StudioPro.jsx", {}, { router: true });
  assert.match(html, /pro-cols/);
  assert.match(html, /pro-side/);
  assert.match(html, /pro-main/);
  assert.match(html, /pro-build/);
  // 코치가 조건 판 **앞** 에 그려지면(DOM 순서) 두 열이 아니라 위아래로 쌓인 것이다.
  assert.ok(html.indexOf("pro-build") < html.indexOf("pro-side"), "조건 판이 코치보다 먼저 와야 한다");
});

test("코치 패널이 StudioPro 소스에 실제로 끼워져 있다", () => {
  assert.match(SOURCE, /import CoachPanel from "\.\.\/components\/CoachPanel\.jsx"/);
  assert.match(SOURCE, /<CoachPanel/);
  // 패치는 병합이다 — 덮어쓰면 코치가 앞서 채운 칸이 매 턴 사라진다.
  assert.match(SOURCE, /setForm\(\(previous\) => \(\{ \.\.\.previous, \.\.\.patch \}\)\)/);
  assert.match(SOURCE, /onPatch=\{applyCoachPatch\}/);
  assert.match(SOURCE, /onDone=/);
  // 프로 판 변형이 그대로여야 한다 — 두 열로 옮기면서 variant 를 잃으면 비중 · 레그 규칙 칸이 사라진다.
  assert.match(SOURCE, /<Builder form=\{form\} setForm=\{setForm\} variant="pro"/);
});

test("1행 주석의 자리맡김이 사라졌다", () => {
  assert.doesNotMatch(SOURCE, /코치 패널 자리는 별도 계획에서 채운다/);
});

test("방금 바꾼 칸을 묶음 이름 한 줄로 알려 준다", async () => {
  const { patchGroups } = await loadPage("../src/pages/StudioPro.jsx");
  assert.deepEqual(patchGroups({ rule_type: "E", candle_interval: "4h" }), ["규칙", "봉 간격"]);
  assert.deepEqual(patchGroups({ symbol: "BTCUSDT, ETHUSDT" }), ["종목"]);
  assert.deepEqual(patchGroups({ use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20 }), ["진입 조건"]);
  assert.deepEqual(patchGroups({ initial_capital: 1000000, amount_per_buy: 50000 }), ["시작 자금"]);
  // 규칙 패치에는 그 규칙의 세부 값이 함께 온다 — 묶음 이름은 '규칙' 하나로 줄인다.
  assert.deepEqual(patchGroups({ rule_type: "H", base_order_size: 1, max_safety_orders: 5 }), ["규칙", "시작 자금"]);
  assert.deepEqual(patchGroups({ trail_percent: 3, entry_dip: 3 }), ["규칙"]);
  assert.deepEqual(patchGroups({}), []);
});

test("두 열 CSS 가 좁은 화면에서 한 열로 내려온다", () => {
  const css = readFileSync(new URL("../src/pages/StudioPro.css", import.meta.url), "utf8");
  assert.match(css, /\.pro-cols/);
  assert.match(css, /@media \(min-width: 1100px\)/);
  assert.match(css, /grid-template-columns: minmax\(0, 1fr\) 340px/);
  assert.match(css, /position: sticky/);
});
