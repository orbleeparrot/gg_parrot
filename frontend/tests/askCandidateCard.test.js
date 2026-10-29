// 종목 후보 카드 — 30일 창(2026-10-29). 순서를 30일 기준으로 바꿔 놓고 화면에는
// '하루 변동' 만 보이면, 사용자가 보는 숫자가 순서와 아무 관계가 없어진다.
// 받은 피드백이 "이미 어느정도 고점에 올라간 코인들을 추천목록으로 보여주는 것 같아"
// 였으므로, 카드가 그 의심에 답해야 한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const dialog = () => source("../src/components/AskParrotDialog.jsx");

test("카드가 지금 가격이 한 달 범위에서 어디쯤인지 말한다", () => {
  assert.match(dialog(), /range_note/,
    "서버가 만든 문장을 그대로 띄운다 — 숫자는 서버가 붙이고 AI 는 문장만 쓴다");
});

test("카드에 보이는 숫자가 순서를 정하는 자와 같다", () => {
  const text = dialog();
  assert.match(text, /chop_30d/, "30일 창으로 줄 세웠으면 그 숫자를 보여줘야 한다");
  assert.match(text, /한 달/, "어느 기간 이야기인지 라벨에 있어야 한다");
});

test("일봉이 없을 때만 하루 변동으로 돌아간다", () => {
  // 캐시가 비어 있는 첫 요청은 기존 24시간 경로를 탄다(ask_daily 머리글).
  // 그때는 예전 숫자가 맞다.
  assert.match(dialog(), /chop_30d\s*==\s*null[\s\S]{0,300}range_pct/,
    "30일 숫자가 없을 때의 대체 경로가 있어야 한다");
});

test("위치 퍼센트를 날숫자로 띄우지 않는다", () => {
  // 'range_pos_30d 85%' 는 설명이 필요한 값이다 — 문장이 그 뜻을 담는다.
  assert.doesNotMatch(dialog(), /\{c\.range_pos_30d\}/,
    "위치는 문장으로만 말한다(range_note)");
});
