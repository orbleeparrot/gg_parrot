// 종목마다 규칙 바꾸기 — 프로(dense) 빌더의 종목 행에서만 보인다.
// 폼은 defaultForm() 위에 얹는다(손으로 짜면 키가 빠진다). 종목 행(SymbolPicker)은 dense 에만 있다.
// LegRuleEditor 는 Builder 를 쓰고 Builder 는 LegRuleEditor 를 쓴다 — scope="leg" 가 그 고리를 끊는다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";
import { defaultForm, withTypeDefaults, RULE_TYPES } from "../src/lib/macro.js";

const form = (over = {}) => ({ ...defaultForm(), rule_type: "I", symbol: "BTCUSDT, ETHUSDT", ...over });
const legRule = (rt = "E", over = {}) => ({ ...withTypeDefaults(defaultForm(), rt), ...over });

function builder(f, extra = {}) {
  return renderComponent("src/components/Builder.jsx", { form: f, setForm: () => {}, variant: "dense", ...extra });
}
const count = (text, re) => (text.match(re) || []).length;

test("프로 빌더는 종목마다 규칙 바꾸기가 있다", async () => {
  const text = textOf(await builder(form()));
  assert.equal(count(text, /규칙 바꾸기/g), 2);
});

test("기본 빌더에는 종목 행이 없으니 규칙 바꾸기도 없다", async () => {
  const text = textOf(await builder(form(), { variant: "default" }));
  assert.doesNotMatch(text, /규칙 바꾸기/);
});

test("scope=leg 로 그리면 규칙 바꾸기가 없다 — Builder 와 LegRuleEditor 의 재귀를 끊는 자리", async () => {
  const text = textOf(await builder(form(), { scope: "leg" }));
  assert.doesNotMatch(text, /규칙 바꾸기/);
  assert.doesNotMatch(text, /종목 검색/);
});

test("scope=leg 는 규칙 판만 그린다 — 거래소 · 기간 · 위험 · 비용 · 레버리지 · 묶음 한도가 없다", async () => {
  const text = textOf(await builder(form({ use_bundle_risk: true }), { scope: "leg" }));
  assert.match(text, /매매 방식/);
  assert.match(text, /전략 조건/);
  for (const gone of [/거래소/, /테스트 기간/, /손실 제한/, /고급 위험 관리/, /거래 비용/, /레버리지/, /묶음 한도/, /시작 자금/]) {
    assert.doesNotMatch(text, gone);
  }
});

test("scope=leg 라도 진입 조건은 그린다", async () => {
  const text = textOf(await builder(legRule("E", { use_entry_filter: true }), { scope: "leg" }));
  assert.match(text, /진입 조건 더 달기/);
  assert.match(text, /진입 조건 종류/);
});

test("종목이 하나면 규칙 바꾸기가 없다", async () => {
  const text = textOf(await builder(form({ symbol: "BTCUSDT" })));
  assert.doesNotMatch(text, /규칙 바꾸기/);
});

test("레그 규칙이 정해진 종목은 그 규칙 이름이 보이고, 나머지는 그대로다", async () => {
  const text = textOf(await builder(form({ leg_rules: { BTCUSDT: legRule("E") } })));
  assert.ok(text.includes(RULE_TYPES.E.label), "정한 규칙 이름");
  assert.equal(count(text, /규칙 바꾸기/g), 1, "ETH 는 아직 묶음 기본 규칙");
});

test("규칙 이름을 모르는 규칙 타입이면 규칙 바뀜", async () => {
  const text = textOf(await builder(form({ leg_rules: { BTCUSDT: legRule("E", { rule_type: "Z" }) } })));
  assert.match(text, /규칙 바뀜/);
});

test("종목 행의 비중 입력 · 합 경고 · 균등하게 버튼이 규칙 버튼과 함께 남는다", async () => {
  const html = await builder(form({ leg_weights: "50, 30" }));
  assert.equal(count(html, /class="bd-symrow-w num"/g), 2);
  assert.match(textOf(html), /비중의 합이 80% 예요/);
  assert.match(textOf(html), /균등하게/);
  assert.equal(count(html, /class="bd-symrow-main"/g), 2);
});

test("LegRuleEditor 는 규칙 판과 되돌리기를 그린다", async () => {
  const html = await renderComponent("src/components/LegRuleEditor.jsx", {
    symbol: "BTCUSDT", rule: legRule("E"), onChange: () => {}, onClear: () => {},
  });
  const text = textOf(html);
  assert.match(text, /묶음 기본 규칙으로/);
  assert.match(text, /매매 방식/);
  assert.match(text, /고점에서 허용할 하락폭/);
  assert.match(html, /aria-label="BTCUSDT 규칙"/);
  assert.doesNotMatch(text, /종목 검색/);
});

test("LegRuleEditor 에 거래소를 주면 그 거래소 기준으로 그린다(국내는 K 불가)", async () => {
  const text = textOf(await renderComponent("src/components/LegRuleEditor.jsx", {
    symbol: "KRW-BTC", rule: legRule("E"), exchange: "upbit", onChange: () => {}, onClear: () => {},
  }));
  assert.match(text, /국내 현물 불가/);
});
