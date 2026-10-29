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
  // 줄 세우는 자는 24시간 headroom 이다(ask_candidates.ORDER_DEFAULT — 측정으로 정했다).
  // 화면에 30일 숫자를 띄우면 사용자가 보는 숫자가 순서와 아무 관계가 없어진다.
  const text = dialog();
  assert.match(text, /range_pct/, "순서를 정하는 24시간 쪽 숫자를 보여줘야 한다");
  assert.doesNotMatch(text, /chop_30d/,
    "30일 오르내림은 순서를 정하지 않으므로 카드에 띄우지 않는다");
});

test("위치 퍼센트는 화면에 아예 쓰지 않는다", () => {
  // 'range_pos_30d 85%' 는 설명이 필요한 값이다 — 문장(range_note)이 그 뜻을 담는다.
  // 특정 표기를 금지하면 {Math.round(c.range_pos_30d)} 같은 변형이 그냥 빠져나간다.
  // 이 칸을 화면에서 **아예 안 쓰는 것**이 지키려는 불변식이다.
  assert.doesNotMatch(dialog(), /range_pos_30d/,
    "위치는 문장으로만 말한다(range_note)");
});
