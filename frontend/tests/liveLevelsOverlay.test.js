// 돌고 있는 전략이 지금 보는 선 — 판단하는 쪽(서버 엔진 · 실행기)이 낸 값만 그린다.
//
// 실행기가 스스로 판단하면 열한 방식 전부의 선을 올려 보내는데, 화면은 오랫동안 셋만
// 그렸다(진입 목표 · 트레일링 스탑 · 트레일 발동). 나머지는 받아 놓고 버렸다.
import test from "node:test";
import assert from "node:assert/strict";
import { liveLevelsOverlay, computeSessionOverlay } from "../src/lib/indicators.js";

const EMPTY = {
  entry_target: null, exit_target: null, stop_loss: null, trail_stop: null,
  trail_activation: null, armed: false, reference: null, band_upper: null,
  band_lower: null, next_buy_ms: null, indicator: null, pending: false,
};

const labels = (overlay) => (overlay?.priceLines || []).map((line) => line.label);
const prices = (overlay) => (overlay?.priceLines || []).map((line) => line.price);

test("엔진이 낸 선만 그린다 — 없는 선은 아예 빠진다", () => {
  assert.equal(liveLevelsOverlay(null), null);
  assert.equal(liveLevelsOverlay({ ...EMPTY }), null);
});

test("예전부터 그리던 셋은 그대로", () => {
  const overlay = liveLevelsOverlay(
    { ...EMPTY, entry_target: 90, trail_stop: 95, trail_activation: 110 }, 100);
  assert.deepEqual(prices(overlay), [90, 95, 110]);
  assert.ok(labels(overlay)[0].startsWith("진입 목표"));
  assert.ok(labels(overlay)[1].startsWith("트레일링 스탑"));
  assert.ok(labels(overlay)[2].startsWith("트레일 발동"));
});

test("파는 선과 손절선도 그린다", () => {
  // 익절/손절 재진입(A)·변동성 돌파(I) 등은 이 둘이 주인공인데 화면에 없었다.
  const overlay = liveLevelsOverlay({ ...EMPTY, exit_target: 110, stop_loss: 92 }, 100);
  assert.deepEqual(prices(overlay), [110, 92]);
  assert.ok(labels(overlay).some((l) => l.startsWith("파는 선")));
  assert.ok(labels(overlay).some((l) => l.startsWith("손절선")));
});

test("기준선과 볼린저 밴드도 그린다", () => {
  const overlay = liveLevelsOverlay(
    { ...EMPTY, reference: 100, band_upper: 108, band_lower: 92 }, 100);
  assert.deepEqual(prices(overlay), [100, 108, 92]);
  assert.ok(labels(overlay).some((l) => l.startsWith("기준선")));
  assert.ok(labels(overlay).some((l) => l.startsWith("위 밴드")));
  assert.ok(labels(overlay).some((l) => l.startsWith("아래 밴드")));
});

test("격자 칸은 지금 가격에서 가까운 둘만", () => {
  // 칸을 전부 그리면 차트가 줄무늬가 되어 아무것도 읽을 수 없다.
  const overlay = liveLevelsOverlay(
    { ...EMPTY, buy_levels: [10, 50, 95, 99, 300], sell_levels: [101, 105, 500] }, 100);
  assert.deepEqual(prices(overlay), [99, 95, 101, 105]);
});

test("격자 범례는 사고파는 쪽마다 하나씩만", () => {
  const overlay = liveLevelsOverlay(
    { ...EMPTY, buy_levels: [95, 99], sell_levels: [101, 105] }, 100);
  const grid = overlay.legend.filter((item) => item.label.startsWith("격자"));
  assert.deepEqual(grid.map((item) => item.label), ["격자 매수", "격자 매도"]);
});

test("가격이 아닌 값은 선이 되지 않는다", () => {
  const bad = {
    ...EMPTY, entry_target: 0, exit_target: -1, stop_loss: Number.NaN,
    reference: null, band_upper: "", buy_levels: [0, -5, Number.NaN],
  };
  assert.equal(liveLevelsOverlay(bad, 100), null);
});

test("진입 목표 설명은 선이 위에 있는지 아래 있는지에 따라 다르다", () => {
  // 변동성 돌파는 선이 **위**에 있고 뚫고 올라오면 산다. 눌림목은 **아래**에 있고
  // 내려오면 산다. 한 문장으로 쓰면 둘 중 하나는 거짓말이 된다(파일럿에서 실제로 혼동).
  const above = liveLevelsOverlay({ ...EMPTY, entry_target: 110 }, 100);
  assert.match(above.note, /넘어서면/);
  const below = liveLevelsOverlay({ ...EMPTY, entry_target: 90 }, 100);
  assert.match(below.note, /내려오면/);
});

test("선이 하나도 없고 준비 중이면 범례로만 알린다", () => {
  const overlay = liveLevelsOverlay({ ...EMPTY, pending: true }, 100);
  assert.deepEqual(overlay.priceLines, []);
  assert.equal(overlay.pending, true);
  assert.deepEqual(overlay.legend.map((item) => item.label), ["가격선 준비 중"]);
});

test("세션 오버레이가 지금 가격을 넘겨 격자를 고른다", () => {
  // computeSessionOverlay 가 price 를 안 넘기면 격자가 엉뚱한 칸을 고른다.
  const candles = Array.from({ length: 5 }, (_, i) => ({
    t: 1767225600000 + i * 60000, o: 100, h: 101, l: 99, c: 100, closed: true,
  }));
  // 가격(100)을 안 넘기면 0 에서 가까운 10·50 을 고른다 — 화면 밖의 엉뚱한 칸이다.
  const levels = { ...EMPTY, buy_levels: [10, 50, 98, 99], sell_levels: [101, 102, 500] };
  const overlay = computeSessionOverlay({ rule_type: "D" }, 0, "long", candles, levels);
  assert.deepEqual(prices(overlay), [99, 98, 101, 102]);
});
