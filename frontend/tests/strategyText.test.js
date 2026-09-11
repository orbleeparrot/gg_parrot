import test from "node:test";
import assert from "node:assert/strict";
import { strategyPhrases } from "../src/lib/strategyText.js";

const flat = (phrases) => phrases.map((tokens) => tokens.map((token) => (token.t === "num" ? `[${token.v}${token.tone ? ":" + token.tone : ""}]` : token.v)).join(""));

test("그리드 문장: 범위·격자 수는 숫자, 손절은 다른 구로 나뉜다", () => {
  const phrases = strategyPhrases("50,000~70,000 구간 20격자 그리드 · -3% 손절");
  assert.equal(phrases.length, 2);
  assert.deepEqual(flat(phrases), ["[50,000~70,000] 구간 [20]격자 그리드", "[-3%:down] 손절"]);
});

test("익절/손절 재진입: 부호에 따라 등락색이 붙는다", () => {
  const phrases = strategyPhrases("평단 대비 +5% 익절 / -3% 손절 후 재진입");
  assert.deepEqual(flat(phrases), ["평단 대비 [+5%:up] 익절 / [-3%:down] 손절 후 재진입"]);
});

test("볼린저·RSI·이동평균: 괄호 안 숫자와 σ, 화살표 앞 숫자를 뗀다", () => {
  assert.deepEqual(flat(strategyPhrases("볼린저(20, 2σ) 돌파")), ["볼린저([20], [2σ]) 돌파"]);
  assert.deepEqual(flat(strategyPhrases("RSI(14) 30↓ 진입 / 70↑ 청산")), ["RSI([14]) [30]↓ 진입 / [70]↑ 청산"]);
  assert.deepEqual(flat(strategyPhrases("SMA 7/25 골든크로스")), ["SMA [7]/[25] 골든크로스"]);
});

test("유니코드 마이너스와 배수·레버리지 구도 처리한다", () => {
  const phrases = strategyPhrases("롱 → −5% 하락 시 50% 매도 후 숏 전환 (숏 익절 3% / 손절 2%) · 3배 레버리지(격리)");
  assert.equal(phrases.length, 2);
  assert.deepEqual(flat(phrases)[0], "롱 → [−5%:down] 하락 시 [50%] 매도 후 숏 전환 (숏 익절 [3%] / 손절 [2%])");
  assert.deepEqual(flat(phrases)[1], "[3]배 레버리지(격리)");
});

test("빈 문장은 빈 배열", () => {
  assert.deepEqual(strategyPhrases(""), []);
  assert.deepEqual(strategyPhrases(null), []);
});
