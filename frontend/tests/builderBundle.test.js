// 묶음 화면 — 종목 행의 비중 입력 · 합 경고 · 균등하게 버튼 · 묶음 한도 칸.
// 폼은 defaultForm() 위에 얹는다(손으로 짜면 키가 빠진다). 종목 고르기(SymbolPicker)는 촘촘한 판(dense)에만 있다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";
import { defaultForm } from "../src/lib/macro.js";

const form = (over = {}) => ({ ...defaultForm(), rule_type: "I", symbol: "BTCUSDT, ETHUSDT, SOLUSDT", ...over });

function render(f, variant = "dense") {
  return renderComponent("src/components/Builder.jsx", { form: f, setForm: () => {}, variant });
}

test("묶음이면 종목마다 비중 입력이 보이고 균등값이 채워진다", async () => {
  const html = await render(form());
  const inputs = [...html.matchAll(/<input[^>]*class="bd-symrow-w num"[^>]*>/g)].map((m) => m[0]);
  assert.equal(inputs.length, 3);
  assert.match(inputs[0], /aria-label="BTC 비중\(%\)"/);
  assert.match(inputs[0], /value="33.33"/);
  assert.match(inputs[2], /value="33.34"/);
});

test("손댄 비중은 그 값이 입력에 보인다", async () => {
  const html = await render(form({ leg_weights: "50, 30, 20" }));
  assert.match(html, /value="50"/);
  assert.match(html, /value="30"/);
  assert.match(textOf(html), /종목마다 비중을 정했어요/);
});

test("종목이 하나면 비중 입력이 없다", async () => {
  const html = await render(form({ symbol: "BTCUSDT" }));
  assert.doesNotMatch(html, /<input[^>]*bd-symrow-w/);
  assert.doesNotMatch(textOf(html), /균등하게$|균등하게 /);
});

test("묶음 한도를 켜면 묶음 한도 칸이 보인다", async () => {
  const text = textOf(await render(form({ use_bundle_risk: true })));
  assert.match(text, /묶음 한도 쓰기/);
  assert.match(text, /묶음 동시 보유 종목 수/);
  assert.match(text, /묶음 총 노출 한도/);
});

test("묶음 한도를 끄면 체크만 있고 칸은 없다", async () => {
  const text = textOf(await render(form()));
  assert.match(text, /묶음 한도 쓰기/);
  assert.doesNotMatch(text, /묶음 동시 보유/);
});

test("종목이 하나면 묶음 한도 칸이 없다", async () => {
  const text = textOf(await render(form({ symbol: "BTCUSDT", use_bundle_risk: true })));
  assert.doesNotMatch(text, /묶음 한도 쓰기/);
  assert.doesNotMatch(text, /묶음 동시 보유/);
});

test("묶음 한도 칸은 기본 판(dense 아님)에도 있다", async () => {
  const text = textOf(await render(form({ use_bundle_risk: true }), "default"));
  assert.match(text, /묶음 동시 보유 종목 수/);
});

test("비중 합이 100 이 아니면 한 줄 알린다", async () => {
  const text = textOf(await render(form({ leg_weights: "50, 30, 10" })));
  assert.match(text, /비중의 합이 90% 예요/);
});

test("비중 합이 맞으면 경고가 없다", async () => {
  const text = textOf(await render(form({ leg_weights: "50, 30, 20" })));
  assert.doesNotMatch(text, /비중의 합이/);
});

test("균등하게 버튼이 있다", async () => {
  const text = textOf(await render(form()));
  assert.match(text, /균등하게/);
});

test("묶음 한도 라벨이 레그 위험관리와 섞이지 않는다", async () => {
  const text = textOf(await render(form({ use_bundle_risk: true })));
  // '묶음' 접두어 없이 '동시 보유 종목 수' 만 있으면 사용자가 레그 설정으로 읽는다.
  assert.doesNotMatch(text, /(?<!묶음 )동시 보유 종목 수/);
  assert.doesNotMatch(text, /(?<!묶음 )총 노출 한도/);
});
