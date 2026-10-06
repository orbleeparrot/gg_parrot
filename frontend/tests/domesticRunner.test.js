import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

// 국내(업비트·빗썸) 매크로: 실행기 직접 연결(빠른 실행)은 열렸고, 매크로 파일(.ggm.json)은 영구히 바이낸스 전용이다.
// 두 길을 가르는 문구와 문 잠금을 화면별로 지킨다. 문구는 '아직' 이 아니라 범위로 쓴다(곧 된다는 약속이 아니다).
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8").replace(/\r\n/g, "\n");
const paper = read("../src/components/PaperPanel.jsx");
const dock = read("../src/components/StudioDock.jsx");
const board = read("../src/pages/Leaderboard.jsx");
const hero = read("../src/components/HeroGuideScreens.jsx");
const start = read("../src/pages/Start.jsx");

// source 에서 start 로 시작해 end 직전까지 잘라 낸다 — 못 찾으면 시험이 먼저 깨지게 한다.
function between(source, startMark, endMark) {
  const from = source.indexOf(startMark);
  assert.ok(from >= 0, `없음: ${startMark}`);
  const to = endMark ? source.indexOf(endMark, from + startMark.length) : source.length;
  assert.ok(to > from, `없음: ${endMark}`);
  return source.slice(from, to);
}
// 한 줄(또는 연속 줄) 안에서 시작 표지가 있는 <button ...> 여는 태그 전체
const openingTag = (source, mark) => between(source, mark.startsWith("<button") ? mark : `<button ${mark}`, ">");
const lineWith = (source, mark) => {
  const line = source.split("\n").find((l) => l.includes(mark));
  assert.ok(line, `없음: ${mark}`);
  return line;
};

const BUNDLE_SCOPE = /바이낸스 전용/;
const NOT_YET = /아직 지원하지 않|미지원/;

test("PaperPanel — 빠른 실행은 국내에서 막지 않고, 매크로 파일은 바이낸스 전용으로 막는다", () => {
  const quick = between(paper, "async function quickRun()", "async function downloadMacro()");
  assert.doesNotMatch(quick, /isDomestic|국내/, "빠른 실행 함수에 국내 가드가 남았다");
  assert.match(quick, /saveMyMacro/);
  const file = between(paper, "async function downloadMacro()", "return { quickRun");
  assert.match(file, /isDomestic\(macro\.exchange\)/, "파일 내려받기는 국내에서 막혀 있어야 한다");
  assert.match(file, /매크로 파일[\s\S]{0,120}바이낸스 전용/);
  assert.match(file, /빠른 실행/);
  assert.doesNotMatch(file, NOT_YET);

  const quickButton = openingTag(paper, "onClick={quickRun}");
  assert.doesNotMatch(quickButton, /domestic/, "빠른 실행 버튼이 국내로 잠긴다");
  const fileButton = openingTag(paper, "onClick={downloadMacro}");
  assert.match(fileButton, /disabled=\{[^}]*domestic[^}]*\}/, "파일 버튼은 국내에서 잠겨 있어야 한다");
  assert.match(fileButton, BUNDLE_SCOPE);
  assert.doesNotMatch(fileButton, NOT_YET);
});

test("PaperPanel — 국내 안내 상자는 빠른 실행을 말하고 '아직' 을 쓰지 않는다", () => {
  const title = lineWith(paper, 'domestic ? "국내 거래소');
  assert.match(title, /빠른 실행/);
  const body = lineWith(paper, "원화 현물 · 롱 · 1배");
  assert.match(body, /빠른 실행/);
  assert.match(body, BUNDLE_SCOPE);
  assert.doesNotMatch(paper, /실행기 직접 연결·실거래 주문은 아직/);
  assert.doesNotMatch(paper, /국내 거래소 실행기 직접 연결은 아직 지원하지 않아요/);
  assert.doesNotMatch(paper, /국내 거래소 실거래 실행기 파일은 아직 지원하지 않아요/);
});

test("StudioDock — 빠른 실행 행은 국내에서 막지 않고, 파일 행은 바이낸스 전용으로 막는다", () => {
  const quickButton = openingTag(dock, "type=\"button\" onClick={quickRun}");
  assert.doesNotMatch(quickButton, /domestic/);
  const quickRow = between(dock, "onClick={quickRun}", "onClick={downloadMacro}");
  assert.doesNotMatch(quickRow, /domestic|미지원|아직/, "빠른 실행 행의 부제가 국내 미지원으로 남았다");

  const fileButton = openingTag(dock, "type=\"button\" onClick={downloadMacro}");
  assert.match(fileButton, /disabled=\{[^}]*domestic[^}]*\}/);
  assert.match(fileButton, BUNDLE_SCOPE);
  const fileRow = between(dock, "onClick={downloadMacro}", "onClick={onShare}");
  assert.match(fileRow, /domestic \? "바이낸스 전용/);
  assert.doesNotMatch(fileRow, NOT_YET);
});

test("StudioDock — '국내 거래소 지원 범위' 는 빠른 실행·파일 범위·현물 제약을 같이 말한다", () => {
  const panel = between(dock, 'id="sd-runner-title" className="sd-runner-title">국내 거래소 지원 범위', "</section>");
  assert.match(panel, /빠른 실행/);
  assert.match(panel, /매크로 파일[\s\S]{0,40}바이낸스 전용/);
  assert.match(panel, /숏·선물·레버리지를 사용할 수 없어요/);
  assert.doesNotMatch(panel, /실행기 주문은 아직|실제 계좌 연결과/);
});

test("Leaderboard — 국내 매크로도 빠른 실행에 연결한다", () => {
  assert.doesNotMatch(board, /isDomestic/, "리더보드에 국내 판정이 남았다");
  const use = between(board, "async function useForQuickRun(entry)", "return (\n    <div className=\"leaderboard-page\">");
  assert.match(use, /\/\?run=1&step=1/);
  assert.doesNotMatch(use, /국내|지원하지 않아요/);
  // 언락 직후 빠른 실행 이동도 국내를 걸러 내지 않는다.
  assert.match(board, /if \(quickRunMode && d\.user_macro\?\.id\) \{/);
  const button = between(board, "useForQuickRun(e); }}", "</button>");
  assert.doesNotMatch(button, /disabled=\{[^}]*(isDomestic|exchange)/);
  assert.doesNotMatch(button, /실행기 미지원|미지원/);
  assert.match(button, /이 매크로 사용/);
});

test("HeroGuideScreens — 국내 안내는 현물 제약을 남기고 실행기 연결을 막는다고 쓰지 않는다", () => {
  const note = lineWith(hero, "원화 현물 전용이에요.");
  assert.match(note, /숏·선물·레버리지는 사용할 수 없어요/);
  assert.match(note, /매크로 파일[\s\S]{0,40}바이낸스 전용/);
  assert.match(note, /빠른 실행/);
  assert.doesNotMatch(hero, /국내 거래소 실행기 직접 연결은 아직 지원하지 않아요/);
  assert.doesNotMatch(note, NOT_YET);
});

test("Start — 페이퍼를 마친 국내 매크로도 실행 마법사로 넘긴다", () => {
  const handoff = between(start, "async function handoffToRunner()", "async function advance()");
  assert.doesNotMatch(handoff, /isDomestic|국내|업비트·빗썸/, "핸드오프에 국내 분기가 남았다");
  assert.match(handoff, /navigate\("\/\?run=1&step=2&flow=build"/);
  assert.doesNotMatch(start, /실행기 직접 연결은 아직 지원하지 않아요/);
  assert.doesNotMatch(start, /세션을 마치고 매크로 저장/, "국내만 '저장' 으로 끝나는 버튼 문구가 남았다");
});

test("국내 현물·롱·1배 제약 문구는 어느 화면에서도 남는다", () => {
  assert.match(hero, /숏·선물·레버리지는 사용할 수 없어요/);
  assert.match(dock, /숏·선물·레버리지를 사용할 수 없어요/);
  assert.match(paper, /원화 현물 · 롱 · 1배/);
});
