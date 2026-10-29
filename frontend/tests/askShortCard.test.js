// 물어볼까 숏 카드 — 조건 한 줄이 방향을 따라가야 한다 (2026-10-30).
//
// 왜 시험하나: `conditionLines` 는 "하단 매수" · "골든크로스 매수" · "RSI 30 아래 매수" 처럼
// **롱의 말**로 적혀 있었다. 숏 카드에 그대로 쓰면 카드가 거짓을 말한다 — 엔진의 숏은
// 상단에서 공매도하고(볼린저), 데드크로스에서 들어가고(이동평균), 과매수에서 들어간다(RSI).
// 엔진 근거: backend/engine/candles.py 의 RSISim · BollingerSim · MACrossSim `_signal`.
import assert from "node:assert/strict";
import test from "node:test";
import { conditionLines, marketLabel, sideLabel, shortNote } from "../src/lib/askView.js";

const macro = (rule_type, params, extra = {}) => ({
  rule_type, params, risk: {}, exchange: "binance", candle_interval: "1h",
  market: "futures", leverage: 2, position_side: "long", ...extra,
});
const short = (rule_type, params, extra = {}) =>
  macro(rule_type, params, { ...extra, position_side: "short" });

test("conditionLines: RSI short enters where the long exits", () => {
  const p = { rsi_period: 14, entry_threshold: 30, exit_threshold: 70 };
  assert.deepEqual(conditionLines(macro("F", p)).slice(0, 2),
    ["RSI(14) 30 아래 매수", "70 위 매도"]);
  assert.deepEqual(conditionLines(short("F", p)).slice(0, 2),
    ["RSI(14) 70 위 공매도", "30 아래 청산"]);
});

test("conditionLines: moving-average short enters on the dead cross", () => {
  const p = { ma_type: "EMA", fast_period: 10, slow_period: 30 };
  assert.deepEqual(conditionLines(macro("J", p)).slice(0, 2),
    ["EMA 10/30 골든크로스 매수", "데드크로스 매도"]);
  assert.deepEqual(conditionLines(short("J", p)).slice(0, 2),
    ["EMA 10/30 데드크로스 공매도", "골든크로스 청산"]);
});

test("conditionLines: Bollinger follows both the strategy and the side", () => {
  // 되돌림(reversion)은 하단에서 사고 돌파(breakout)는 상단에서 산다 — 예전 문구는
  // strategy 를 보지 않아 돌파 프리셋에서도 "하단 매수" 라고 적었다(엔진과 반대).
  const rev = { bb_period: 20, bb_std: 2, strategy: "reversion", exit_target: "mid" };
  const brk = { bb_period: 20, bb_std: 2, strategy: "breakout", exit_target: "mid" };
  assert.deepEqual(conditionLines(macro("G", rev)).slice(0, 2),
    ["볼린저(20, 2σ) 하단 매수", "중심선 매도"]);
  assert.deepEqual(conditionLines(macro("G", brk)).slice(0, 2),
    ["볼린저(20, 2σ) 상단 매수", "중심선 매도"]);
  assert.deepEqual(conditionLines(short("G", rev)).slice(0, 2),
    ["볼린저(20, 2σ) 상단 공매도", "중심선 청산"]);
  assert.deepEqual(conditionLines(short("G", brk)).slice(0, 2),
    ["볼린저(20, 2σ) 하단 공매도", "중심선 청산"]);
});

test("conditionLines: Bollinger opposite-band exit flips with the side", () => {
  const p = { bb_period: 20, bb_std: 2.5, strategy: "reversion", exit_target: "opposite" };
  assert.equal(conditionLines(macro("G", p))[1], "상단 매도");
  assert.equal(conditionLines(short("G", p))[1], "하단 청산");
});

test("conditionLines: take-profit/stop wording stays direction-neutral", () => {
  // A 의 익절·손절은 진입가 기준 퍼센트라 양쪽에서 같은 말이다. 방향은 배지가 말한다.
  const p = { take_profit_pct: 3 };
  const risk = { risk: { stop_loss_pct: 2 } };
  assert.deepEqual(conditionLines({ ...macro("A", p), ...risk }).slice(0, 2),
    ["익절 +3%", "손절 -2%"]);
  assert.deepEqual(conditionLines({ ...short("A", p), ...risk }).slice(0, 2),
    ["익절 +3%", "손절 -2%"]);
});

test("sideLabel: only a short gets a badge", () => {
  assert.equal(sideLabel(macro("J", {})), "");
  assert.equal(sideLabel(short("J", {})), "숏");
  assert.equal(sideLabel(null), "");
});

test("shortNote: says which way it earns, and warns when leverage is on", () => {
  assert.equal(shortNote(macro("J", {})), "", "롱 카드에는 줄이 붙지 않는다");
  const one = shortNote(short("J", {}, { leverage: 1 }));
  assert.match(one, /내려가면/);
  assert.match(one, /올라가면/, "반대로 갔을 때를 함께 말한다");
  assert.doesNotMatch(one, /청산/, "1배에는 청산 경고를 붙이지 않는다");
  const two = shortNote(short("J", {}, { leverage: 2 }));
  assert.match(two, /2배/);
  assert.match(two, /청산/, "레버리지가 있으면 청산을 말한다");
});

test("shortNote: never predicts a direction", () => {
  // 물어볼까의 설계 — 어디서도 가격이 어느 쪽으로 갈지 말하지 않는다.
  const text = shortNote(short("J", {}, { leverage: 3 }));
  for (const banned of ["내릴 것", "하락할", "오를 것", "전망", "예상", "추천", "보장"]) {
    assert.ok(!text.includes(banned), `'${banned}' 가 들어가면 안 된다: ${text}`);
  }
});

test("marketLabel: unchanged — the side is its own badge, not part of the market tag", () => {
  assert.equal(marketLabel(short("J", {}, { leverage: 2 })), "선물 · 격리 2배");
});
