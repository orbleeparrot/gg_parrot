// 국내(업비트·빗썸) 매크로도 바이낸스처럼 .ggm.json 을 내려받아 실행기에 넣는다.
// 화면에서 막지도, "바이낸스 전용" 이라고 말하지도 않는다. 대신 '실행기 v10 이상' 을 버튼 옆에서 미리 말한다
// (서버는 내려줄 때 실행기 버전을 모르고, 사용자는 파일을 넣은 뒤에야 426 을 본다).
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const DOMESTIC_MACRO = {
  exchange: "upbit", symbol: "KRW-BTC", rule_type: "A", position_side: "long",
  leverage: 1, candle_interval: "1d",
  params: { initial_capital: 1000000, take_profit_pct: 3 },
};
// 내려받기 버튼의 여는 태그 — disabled 조건을 보려면 그려낸 글이 아니라 속성이 필요하다.
const buttonWith = (html, label) => {
  const at = html.indexOf(label);
  assert.ok(at > 0, `없음: ${label}`);
  const open = html.lastIndexOf("<button", at);
  assert.ok(open >= 0, `${label} 앞에 <button 이 없다`);
  return html.slice(open, html.indexOf(">", open) + 1);
};

test("PaperPanel — 국내에서도 매크로 파일 내려받기가 열려 있다", async () => {
  const html = await renderComponent("src/components/PaperPanel.jsx", { macro: DOMESTIC_MACRO, valErr: "" },
    { router: true, exportName: "PaperNextSteps" });
  const text = textOf(html);
  assert.doesNotMatch(text, /바이낸스 전용/, "국내 화면에 '바이낸스 전용' 이 남았다");
  assert.match(text, /매크로 파일 내려받기/);
  // 버튼이 국내로 잠기지 않는다 — valErr 만 잠근다.
  const button = buttonWith(html, "매크로 파일 내려받기");
  assert.doesNotMatch(button, /disabled/, "국내 매크로에서 내려받기 버튼이 잠겼다");
  // 내려받기 옆에서 실행기 버전을 한 번은 말한다.
  assert.match(text, /실행기 v10 이상/);
});

test("PaperPanel — 내려받기 함수에 국내 사전 차단이 없다", () => {
  const paper = read("../src/components/PaperPanel.jsx");
  const file = paper.slice(paper.indexOf("async function downloadMacro()"), paper.indexOf("return { quickRun"));
  assert.ok(file.length > 0);
  assert.doesNotMatch(file, /isDomestic/, "내려받기 함수에 국내 가드가 남았다");
  assert.doesNotMatch(file, /바이낸스 전용/);
  assert.match(file, /api\.downloadMacroFile\(macro\)/);
});

test("StudioDock — 국내에서도 매크로 파일 내려받기 행이 열려 있다", async () => {
  const html = await renderComponent("src/components/StudioDock.jsx", { macro: DOMESTIC_MACRO, result: {}, valErr: "" },
    { router: true, exportName: "StudioOutcomes" });
  const text = textOf(html);
  assert.doesNotMatch(text, /바이낸스 전용/);
  assert.match(text, /매크로 파일 내려받기/);
  assert.match(text, /\.ggm\.json 파일로 저장해요/, "국내에서 부제가 바이낸스와 달라졌다");
  const button = buttonWith(html, "매크로 파일 내려받기");
  assert.doesNotMatch(button, /disabled/, "국내 매크로에서 내려받기 행이 잠겼다");
  assert.match(text, /실행기 v10 이상/);
});

test("HeroGuideScreens·api.js — '매크로 파일은 바이낸스 전용' 이라는 말이 어디에도 없다", () => {
  for (const path of ["../src/components/HeroGuideScreens.jsx", "../src/components/PaperPanel.jsx",
    "../src/components/StudioDock.jsx", "../src/api.js"]) {
    const source = read(path);
    assert.doesNotMatch(source, /매크로 파일[^.\n]{0,40}바이낸스 전용/, `${path} 에 거짓 문구가 남았다`);
  }
  assert.match(read("../src/components/HeroGuideScreens.jsx"), /매크로 파일\(\.ggm\.json\) 내려받기와 빠른 실행 둘 다 돼요/);
});

test("StudioPro — 매크로 파일 내려받기 버튼이 검증하기 옆에 있다", async () => {
  const html = await renderComponent("src/pages/StudioPro.jsx", {}, { router: true });
  const text = textOf(html);
  assert.match(text, /매크로 파일 내려받기/);
  assert.match(text, /\.ggm\.json 을 실행기에 넣어 실행해요/);
  // 두 버튼이 같은 줄에 있다 — 검증 판에서 길이 끊기지 않는다.
  assert.match(html, /pro-acts/);
  assert.ok(html.indexOf("검증하기") < html.indexOf("매크로 파일 내려받기"), "검증하기가 먼저 와야 한다");
});

test("StudioPro 소스 — 버튼이 실제로 그 끝점을 쓰고, 누르기 전에 조건을 검증한다", () => {
  const source = read("../src/pages/StudioPro.jsx");
  // 글만 보면 "기능이 반대 화면에 들어간" 것을 놓친다 — 끝점 호출과 버튼의 연결을 소스에서 단정한다.
  assert.match(source, /api\.downloadMacroFile\(buildMacro\(form\)\)/);
  assert.match(source, /onClick=\{downloadFile\}/, "버튼이 내려받기 함수에 묶여 있지 않다");
  const fn = source.slice(source.indexOf("async function downloadFile()"), source.indexOf("async function runValidation()"));
  assert.ok(fn.length > 0, "downloadFile 함수가 없다");
  // 조건이 틀리면 내려받지 않고 그 자리의 오류 문구를 쓴다.
  assert.match(fn, /const problem = validateDetailed\(form\);/);
  assert.ok(fn.indexOf("validateDetailed") < fn.indexOf("api.downloadMacroFile"), "검증이 내려받기보다 먼저여야 한다");
  assert.match(fn, /setFormError\(\{ \.\.\.problem, form \}\);[\s\S]{0,80}return;/);
});

test("StudioPro — 국내 조건이면 실행기 v10 안내가 버튼 옆에 뜬다", async () => {
  const domestic = textOf(await renderComponent("src/pages/StudioPro.jsx", {},
    { router: true, routerState: { macro: DOMESTIC_MACRO, source: "builder-mode" } }));
  assert.match(domestic, /업비트·빗썸은 실행기 v10 이상이 필요해요/);
  // 바이낸스 조건으로 열면 그 줄은 없다 — 모든 화면에 붙는 군더더기가 아니다.
  const binance = textOf(await renderComponent("src/pages/StudioPro.jsx", {}, { router: true }));
  assert.doesNotMatch(binance, /v10/);
});
