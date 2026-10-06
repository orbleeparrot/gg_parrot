// 빌더 화면의 진입 필터 칸 — 소스에 글자가 있느냐가 아니라 실제로 그려진 화면을 본다.
import assert from "node:assert/strict";
import { test } from "node:test";
import { renderComponent, textOf } from "./renderHelper.js";
import { FILTERABLE_RULE_TYPES, defaultForm, validateDetailed, withTypeDefaults } from "../src/lib/macro.js";

const formFor = (rt, extra = {}) => ({ ...withTypeDefaults({ ...defaultForm(), symbol: "BTCUSDT" }, rt), ...extra });
const render = (rt, extra = {}, props = {}) => renderComponent(
  "src/components/Builder.jsx",
  { form: formFor(rt, extra), setForm: () => {}, ...props },
);

const CHECK = /진입 조건 더 달기/;
// 종류별로 화면에 나와야 하는 라벨 — 다른 종류의 칸이 섞여 나오면 안 된다.
const KIND_LABELS = {
  ma: ["이동평균 종류", "이동평균 기간 (봉)", "어느 쪽일 때 살까"],
  rsi: ["RSI 기간 (봉)", "RSI 아래 한도", "RSI 위 한도"],
  bb: ["볼린저 기간 (봉)", "표준편차 배수", "어느 자리일 때 살까"],
  volume: ["거래량 평균 기간 (봉)", "평균의 몇 배 이상"],
};

test("필터를 쓰는 규칙에서는 체크가 보이고, 켜기 전에는 세부 칸이 없다", async () => {
  const text = textOf(await render("I"));
  assert.match(text, CHECK);
  assert.doesNotMatch(text, /진입 조건 종류/);
  for (const labels of Object.values(KIND_LABELS)) {
    for (const label of labels) assert.ok(!text.includes(label), label);
  }
});

test("켜면 종류와 세부 칸이 나온다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "ma" }));
  assert.match(text, /진입 조건 종류/);
  assert.match(text, /이동평균 기간/);
});

test("종류 고르기에 네 종류가 모두 있고 설명이 붙는다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "ma" }));
  for (const label of ["이동평균 위/아래", "RSI 구간", "볼린저 위치", "거래량 배수"]) assert.ok(text.includes(label), label);
  assert.match(text, /이 조건이 아닐 때는 사지 않아요\. 청산은 그대로예요/);
});

test("종류를 바꾸면 그 종류의 칸만 나온다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "volume" }));
  assert.match(text, /평균의 몇 배 이상/);
  assert.doesNotMatch(text, /이동평균 기간/);
});

test("네 종류 각각이 자기 칸만 그린다", async () => {
  for (const [kind, labels] of Object.entries(KIND_LABELS)) {
    const text = textOf(await render("I", { use_entry_filter: true, filter_kind: kind }));
    for (const label of labels) assert.ok(text.includes(label), `${kind}: ${label}`);
    for (const [other, otherLabels] of Object.entries(KIND_LABELS)) {
      if (other === kind) continue;
      for (const label of otherLabels) assert.ok(!text.includes(label), `${kind} 에 ${other} 칸 ${label}`);
    }
  }
});

test("RSI 칸은 한쪽만 채워도 된다고 알려 준다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "rsi" }));
  assert.match(text, /비워두면 아래 한도 없음/);
  assert.match(text, /비워두면 위 한도 없음/);
});

test("필터를 못 쓰는 네 규칙에서는 칸이 아예 없다", async () => {
  for (const rt of ["A", "B", "C", "D"]) {
    assert.doesNotMatch(textOf(await render(rt)), CHECK, rt);
  }
});

test("못 쓰는 규칙은 폼에 켜진 값이 남아 있어도 세부 칸을 그리지 않는다", async () => {
  for (const rt of ["A", "B", "C", "D"]) {
    // withTypeDefaults 를 거치지 않고 켜진 채로 들어온 폼 — 화면이 규칙으로 직접 막아야 한다.
    const text = textOf(await render(rt, { use_entry_filter: true, filter_kind: "ma" }));
    assert.doesNotMatch(text, CHECK, rt);
    assert.doesNotMatch(text, /진입 조건 종류/, rt);
    assert.doesNotMatch(text, /이동평균 기간 \(봉\)/, rt);
  }
});

test("필터를 쓰는 일곱 규칙 모두에서 체크가 보인다", async () => {
  for (const rt of FILTERABLE_RULE_TYPES) {
    assert.match(textOf(await render(rt)), CHECK, rt);
  }
});

test("촘촘한 판에서도 같은 규칙으로 보인다", async () => {
  for (const rt of ["A", "B", "C", "D"]) {
    assert.doesNotMatch(textOf(await render(rt, {}, { variant: "dense" })), CHECK, rt);
  }
  for (const rt of FILTERABLE_RULE_TYPES) {
    const text = textOf(await render(rt, { use_entry_filter: true, filter_kind: "rsi" }, { variant: "dense" }));
    assert.match(text, CHECK, rt);
    assert.match(text, /RSI 기간/, rt);
  }
});

test("체크 상태와 입력값이 폼에서 그려진다", async () => {
  const html = await render("I", { use_entry_filter: true, filter_kind: "ma", filter_ma_period: 50 });
  assert.match(html, /<input id="[^"]*-use_entry_filter" type="checkbox" checked=""\/>/);
  assert.match(html, /value="50"/);
  const off = await render("I");
  assert.match(off, /<input id="[^"]*-use_entry_filter" type="checkbox"\/>/);
});

test("필터 칸은 전략 조건 다음, 손실 제한 앞에 온다", async () => {
  const text = textOf(await render("I", { use_entry_filter: true, filter_kind: "ma" }));
  const params = text.indexOf("전략 조건");
  const filter = text.indexOf("진입 조건 더 달기");
  const risk = text.indexOf("손실 제한");
  const advanced = text.indexOf("고급 위험 관리");
  assert.ok(params >= 0 && filter > params, "전략 조건 다음");
  assert.ok(risk > filter, "손실 제한 앞");
  assert.ok(advanced > filter, "고급 위험 관리 앞");
});

test("검증이 가리키는 칸 키가 화면의 칸 키와 같다", async () => {
  // 검증이 filter_xxx 를 돌려줘도 화면에 그 키의 칸이 없으면 오류가 어디에도 안 뜬다.
  const cases = [
    { filter_kind: "ma", filter_ma_period: 1 },
    { filter_kind: "rsi", filter_rsi_period: 1 },
    { filter_kind: "rsi", filter_rsi_min: "", filter_rsi_max: "" },
    { filter_kind: "rsi", filter_rsi_min: 80, filter_rsi_max: 20 },
    { filter_kind: "bb", filter_bb_period: 1 },
    { filter_kind: "bb", filter_bb_num_std: 6 },
    { filter_kind: "volume", filter_vol_period: 1 },
    { filter_kind: "volume", filter_vol_multiple: 0 },
  ];
  for (const extra of cases) {
    const form = formFor("I", { use_entry_filter: true, ...extra });
    const error = validateDetailed(form);
    assert.ok(error && error.field.startsWith("filter_"), JSON.stringify(extra));
    const html = await renderComponent("src/components/Builder.jsx", { form, setForm: () => {}, variant: "dense", fieldError: error });
    assert.ok(html.includes(`data-field="${error.field}"`), `${error.field} 칸이 없다`);
    assert.ok(textOf(html).includes(error.message), `${error.field} 문구가 안 뜬다`);
  }
});

test("필터를 꺼 둔 기존 화면의 칸은 그대로 남는다", async () => {
  const text = textOf(await render("I"));
  // 기존 칸은 그대로
  for (const label of ["돌파 기준 계수", "정리 기준", "이동평균 필터 기간", "손실 제한", "고급 위험 관리"]) {
    assert.ok(text.includes(label), label);
  }
});
