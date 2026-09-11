import test from "node:test";
import assert from "node:assert/strict";
import { strategyPhrases } from "../src/lib/strategyText.js";

// 숫자 토큰은 [값] 으로 표시해 구 단위 문자열로 편다.
const flat = (phrases) => phrases.map((tokens) => tokens.map((token) => (token.t === "num" ? `[${token.v}]` : token.v)).join(""));
const nums = (phrases) => phrases.flat().filter((token) => token.t === "num").map((token) => token.v);

test("A 익절/손절 후 재진입 — 롱과 숏 문장", () => {
  assert.deepEqual(flat(strategyPhrases("평단 대비 +5% 익절 / -3% 손절 후 재진입")), ["평단 대비 [+5%] 익절 / [-3%] 손절 후 재진입"]);
  assert.deepEqual(nums(strategyPhrases("평단 대비 -5% 하락 익절 / +3% 상승 손절 후 재진입")), ["-5%", "+3%"]);
});

test("B 지정가 밴드 — 가격 둘과 손절이 구로 나뉜다", () => {
  const long = strategyPhrases("50,000 이하 매수 / 60,000 이상 매도 · -3% 손절");
  assert.deepEqual(flat(long), ["[50,000] 이하 매수 / [60,000] 이상 매도", "[-3%] 손절"]);
  assert.deepEqual(nums(strategyPhrases("60,000 이상 숏 진입 / 50,000 이하 청산")), ["60,000", "50,000"]);
});

test("C 정기 분할매수 — 간격 일수와 회당 금액", () => {
  assert.deepEqual(flat(strategyPhrases("7일마다 100 분할매수(DCA)")), ["[7]일마다 [100] 분할매수(DCA)"]);
});

test("D 그리드 — 범위는 한 토큰, 격자 수는 숫자, 손절은 다른 구", () => {
  const phrases = strategyPhrases("50,000~70,000 구간 20격자 그리드 · -3% 손절");
  assert.equal(phrases.length, 2);
  assert.deepEqual(flat(phrases), ["[50,000~70,000] 구간 [20]격자 그리드", "[-3%] 손절"]);
});

test("E 트레일링 스탑 — 발동 수익률과 되돌림 폭", () => {
  assert.deepEqual(flat(strategyPhrases("+3% 이후 1.5% 트레일링 스탑")), ["[+3%] 이후 [1.5%] 트레일링 스탑"]);
});

test("F RSI — 기간·진입·청산 임계값 (영문 뒤 괄호 안 숫자도 뗀다)", () => {
  assert.deepEqual(flat(strategyPhrases("RSI(14) 30↓ 진입 / 70↑ 청산")), ["RSI([14]) [30]↓ 진입 / [70]↑ 청산"]);
});

test("G 볼린저 — 기간과 σ, 쉼표는 숫자에 딸려 오지 않는다", () => {
  assert.deepEqual(flat(strategyPhrases("볼린저(20, 2σ) 돌파")), ["볼린저([20], [2σ]) 돌파"]);
  assert.deepEqual(nums(strategyPhrases("볼린저(20, 2.5σ) 역추세")), ["20", "2.5σ"]);
});

test("H 마틴게일 — 기본 주문·세이프티 횟수·익절", () => {
  assert.deepEqual(flat(strategyPhrases("기본 100 + 세이프티 5회 물타기 / +1.5% 익절")), ["기본 [100] + 세이프티 [5]회 물타기 / [+1.5%] 익절"]);
});

test("I 변동성 돌파 — k 값", () => {
  assert.deepEqual(flat(strategyPhrases("변동성 돌파 (k=0.5)")), ["변동성 돌파 (k=[0.5])"]);
});

test("J 이동평균 — 빠른/느린 기간 (SMA·EMA 뒤 공백 숫자)", () => {
  assert.deepEqual(flat(strategyPhrases("SMA 7/25 골든크로스")), ["SMA [7]/[25] 골든크로스"]);
  assert.deepEqual(nums(strategyPhrases("EMA 12/26 골든크로스")), ["12", "26"]);
});

test("K 하락 방어 전환 — 유니코드 마이너스·부분 매도·숏 익절/손절, 레버리지 구", () => {
  const phrases = strategyPhrases("롱 → −5% 하락 시 50% 매도 후 숏 전환 (숏 익절 3% / 손절 2%) · 3배 레버리지(격리)");
  assert.equal(phrases.length, 2);
  assert.deepEqual(flat(phrases), ["롱 → [−5%] 하락 시 [50%] 매도 후 숏 전환 (숏 익절 [3%] / 손절 [2%])", "[3]배 레버리지(격리)"]);
});

test("dropLeverage — 카드 머리 태그와 겹치는 레버리지 구를 뺀다", () => {
  const text = "볼린저(20, 2σ) 돌파 · -3% 손절 · 3배 레버리지(격리)";
  assert.equal(strategyPhrases(text).length, 3);
  assert.deepEqual(flat(strategyPhrases(text, { dropLeverage: true })), ["볼린저([20], [2σ]) 돌파", "[-3%] 손절"]);
});

test("부호에 따른 등락 정보는 남겨 둔다", () => {
  const tones = strategyPhrases("평단 대비 +5% 익절 / -3% 손절 후 재진입").flat().filter((t) => t.t === "num").map((t) => t.tone);
  assert.deepEqual(tones, ["up", "down"]);
});

test("빈 문장은 빈 배열", () => {
  assert.deepEqual(strategyPhrases(""), []);
  assert.deepEqual(strategyPhrases(null), []);
});
