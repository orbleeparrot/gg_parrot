// 묶음 화면 — 종목 행의 비중 입력 · 합 경고 · 균등하게 버튼 · 묶음 한도 칸.
// 폼은 defaultForm() 위에 얹는다(손으로 짜면 키가 빠진다).
//
// 비중 입력 · 레그 규칙 · 묶음 한도는 **프로 빌더(variant="pro")** 의 것이다. 종목 고르기(SymbolPicker)는
// 촘촘한 판(dense = 기본 빌더의 좁은 조건 칸)에도 있지만, 거기서는 비중이 읽기 전용(1/N)이다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";
import { defaultForm } from "../src/lib/macro.js";

const form = (over = {}) => ({ ...defaultForm(), rule_type: "I", symbol: "BTCUSDT, ETHUSDT, SOLUSDT", ...over });

// 화면이 실제로 넘기는 변형으로 그린다 — "pro"/"dense" 를 손으로 적으면, 화면이 반대로 가 있어도
// 시험이 초록이다. 실제로 그 일이 있었다: 시험이 `variant: "dense"` 를 "프로 빌더" 라고 적어 둬서
// 비중 입력이 기본 빌더에만 있는 위반이 674개 시험을 전부 통과했다.
const variantOf = (page) => {
  const src = readFileSync(new URL(`../src/pages/${page}`, import.meta.url), "utf8");
  const match = /<Builder[^>]*?\svariant="([^"]+)"/.exec(src);
  return match ? match[1] : "default"; // variant 를 안 넘기면 기본 변형이다
};

// 프로 빌더 = StudioPro 가 넘기는 변형. 기본 빌더 = Studio 가 넘기는 변형("촘촘한 판").
const renderPro = (f) => renderComponent("src/components/Builder.jsx",
  { form: f, setForm: () => {}, variant: variantOf("StudioPro.jsx") });
const renderBasic = (f) => renderComponent("src/components/Builder.jsx",
  { form: f, setForm: () => {}, variant: variantOf("Studio.jsx") });

const weightInputs = (html) => [...html.matchAll(/<input[^>]*class="bd-symrow-w num"[^>]*>/g)].map((m) => m[0]);
const valueOf = (tag) => (/value="([^"]*)"/.exec(tag) || [, null])[1];

test("프로 빌더에 비중 입력과 묶음 한도가 있다", async () => {
  const html = await renderPro(form({ use_bundle_risk: true }));
  const inputs = weightInputs(html);
  assert.equal(inputs.length, 3, "종목마다 비중 입력 하나");
  assert.match(inputs[0], /aria-label="BTC 비중\(%\)"/);
  const text = textOf(html);
  assert.match(text, /규칙 바꾸기/, "종목마다 규칙 바꾸기");
  assert.match(text, /묶음 한도 쓰기/);
  assert.match(text, /묶음 동시 보유 종목 수/);
  assert.match(text, /균등하게/, "비중을 균등으로 되돌리는 버튼");
});

test("기본 빌더에는 비중 입력도 레그 규칙도 묶음 한도도 없다", async () => {
  const html = await renderBasic(form({ use_bundle_risk: true }));
  assert.doesNotMatch(textOf(html), /규칙 바꾸기|묶음 동시 보유|균등하게/);
  assert.equal(weightInputs(html).length, 0, "비중은 고칠 수 없다");
  // 읽기 전용 비중 표시는 전처럼 남는다.
  assert.match(textOf(html), /1\/3|33/);
});

test("StudioPro 가 pro 변형을 넘긴다", () => {
  // 소스 문자열 검사라 약하지만 화면이 반대로 간 것을 잡는 유일한 싼 방법이다.
  assert.equal(variantOf("StudioPro.jsx"), "pro");
  // 기본 빌더(Studio)는 촘촘한 판 그대로 — 묶음 기능을 여기로 옮기면 스펙 §10 과 반대가 된다.
  assert.equal(variantOf("Studio.jsx"), "dense");
});

test("묶음이면 종목마다 비중 입력이 보이고 균등값이 채워진다", async () => {
  const inputs = weightInputs(await renderPro(form()));
  assert.equal(inputs.length, 3);
  assert.match(inputs[0], /aria-label="BTC 비중\(%\)"/);
  assert.match(inputs[0], /value="33.33"/);
  assert.match(inputs[2], /value="33.34"/);
});

test("손댄 비중은 그 값이 입력에 보인다", async () => {
  const html = await renderPro(form({ leg_weights: "50, 30, 20" }));
  assert.match(html, /value="50"/);
  assert.match(html, /value="30"/);
  assert.match(textOf(html), /종목마다 비중을 정했어요/);
});

test("비중 칸을 비우면 그 자리만 비고 뒤 칸 숫자가 밀려오지 않는다", async () => {
  // 실측 버그: filter(Boolean) 로 쪼개면 "50, , 33.34" 가 50/33.34/33.34 가 되어
  // 사용자가 타이핑하는 중에 다른 종목의 비중이 바뀐다.
  const inputs = weightInputs(await renderPro(form({ leg_weights: "50, , 33.34" })));
  assert.deepEqual(inputs.map(valueOf), ["50", "", "33.34"]);
});

test("소수점을 치는 중(33.)에도 뒤 칸이 그대로 있다", async () => {
  const inputs = weightInputs(await renderPro(form({ leg_weights: "33., 33.33, 33.34" })));
  assert.deepEqual(inputs.map(valueOf), ["33.", "33.33", "33.34"]);
});

test("빈 칸은 합을 0 으로 세어 합 경고가 뜬다", async () => {
  const text = textOf(await renderPro(form({ leg_weights: "50, , 33.34" })));
  assert.match(text, /비중의 합이 83.34% 예요/);
});

test("종목이 하나면 비중 입력이 없다", async () => {
  const html = await renderPro(form({ symbol: "BTCUSDT" }));
  assert.doesNotMatch(html, /<input[^>]*bd-symrow-w/);
  assert.doesNotMatch(textOf(html), /균등하게$|균등하게 /);
});

test("묶음 한도를 켜면 묶음 한도 칸이 보인다", async () => {
  const text = textOf(await renderPro(form({ use_bundle_risk: true })));
  assert.match(text, /묶음 한도 쓰기/);
  assert.match(text, /묶음 동시 보유 종목 수/);
  assert.match(text, /묶음 총 노출 한도/);
});

test("묶음 한도를 끄면 체크만 있고 칸은 없다", async () => {
  const text = textOf(await renderPro(form()));
  assert.match(text, /묶음 한도 쓰기/);
  assert.doesNotMatch(text, /묶음 동시 보유/);
});

test("종목이 하나면 묶음 한도 칸이 없다", async () => {
  const text = textOf(await renderPro(form({ symbol: "BTCUSDT", use_bundle_risk: true })));
  assert.doesNotMatch(text, /묶음 한도 쓰기/);
  assert.doesNotMatch(text, /묶음 동시 보유/);
});

test("묶음 한도 칸은 등록 모달의 기본 변형에는 없다", async () => {
  // 기본 변형(variant 없음)은 등록 모달이 쓰는 평문 판이다 — 묶음 기능은 프로 빌더의 것이다.
  const text = textOf(await renderComponent("src/components/Builder.jsx",
    { form: form({ use_bundle_risk: true }), setForm: () => {} }));
  assert.doesNotMatch(text, /묶음 동시 보유 종목 수/);
  assert.doesNotMatch(text, /묶음 한도 쓰기/);
});

test("비중 합이 100 이 아니면 한 줄 알린다", async () => {
  const text = textOf(await renderPro(form({ leg_weights: "50, 30, 10" })));
  assert.match(text, /비중의 합이 90% 예요/);
});

test("비중 합이 맞으면 경고가 없다", async () => {
  const text = textOf(await renderPro(form({ leg_weights: "50, 30, 20" })));
  assert.doesNotMatch(text, /비중의 합이/);
});

test("균등하게 버튼이 있다", async () => {
  const text = textOf(await renderPro(form()));
  assert.match(text, /균등하게/);
});

test("묶음 한도 라벨이 레그 위험관리와 섞이지 않는다", async () => {
  const text = textOf(await renderPro(form({ use_bundle_risk: true })));
  // '묶음' 접두어 없이 '동시 보유 종목 수' 만 있으면 사용자가 레그 설정으로 읽는다.
  assert.doesNotMatch(text, /(?<!묶음 )동시 보유 종목 수/);
  assert.doesNotMatch(text, /(?<!묶음 )총 노출 한도/);
});

test("기본 빌더의 종목 행은 읽기 전용 비중을 그대로 보여 준다", async () => {
  const html = await renderBasic(form({ symbol: "BTCUSDT, ETHUSDT" }));
  assert.match(textOf(html), /1\/2 · 50%/);
  assert.match(textOf(html), /자금을 종목 수만큼 똑같이 나눠요/);
});

test("비중을 정한 매크로를 기본 빌더에 들고 오면 그 비중을 읽기 전용으로 보여 준다", async () => {
  // 고칠 수는 없지만 1/N 이라고 적으면 거짓이다 — 70/30 묶음이 50% 로 보인다.
  const html = await renderBasic(form({ symbol: "BTCUSDT, ETHUSDT", leg_weights: "70, 30" }));
  const text = textOf(html);
  assert.match(text, /70%/);
  assert.match(text, /30%/);
  assert.doesNotMatch(text, /1\/2 · 50%/);
  assert.match(text, /종목마다 비중을 정했어요/);
  assert.equal(weightInputs(html).length, 0, "그래도 고칠 수는 없다 — 비중 입력은 프로 빌더의 것이다");
});
