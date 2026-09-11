import test from "node:test";
import assert from "node:assert/strict";
import { candleData, canUpdateCandleData, constrainCandleRange, restoreCandleRange, overlayPriceRange, priceMinMove } from "../src/lib/candleChartData.js";

const rows = Array.from({ length: 300 }, (_, i) => ({ t: 1767225600000 + i * 60000, o: 0.00002345, h: 0.00002578, l: 0.00002198, c: 0.00002467, closed: i < 299 }));
const colors = { upFaded: "rgba(0,119,56,.8)", downFaded: "rgba(200,30,51,.8)" };

test("adapts all OHLC values without rounding or changing the source", () => {
  const before = JSON.stringify(rows);
  const adapted = candleData(rows, colors);
  assert.equal(adapted.length, 300);
  adapted.forEach((bar, i) => assert.deepEqual([bar.time * 1000, bar.open, bar.high, bar.low, bar.close], [rows[i].t, rows[i].o, rows[i].h, rows[i].l, rows[i].c]));
  assert.equal(adapted[0].color, undefined);
  assert.equal(adapted.at(-1).color, colors.upFaded);
  assert.equal(JSON.stringify(rows), before);
});

test("a new bar follows live, while a historical window stays at the same timestamps", () => {
  const next = [...rows.slice(1), { ...rows.at(-1), t: rows.at(-1).t + 60000 }];
  const history = { from: 119.5, to: 199.5 };
  const shifted = restoreCandleRange(history, rows, next, false);
  assert.deepEqual(shifted, { from: 118.5, to: 198.5 });
  assert.equal(next[Math.ceil(shifted.from)].t, rows[Math.ceil(history.from)].t);
  assert.equal(next[Math.floor(shifted.to)].t, rows[Math.floor(history.to)].t);
  assert.deepEqual(restoreCandleRange({ from: 219.5, to: 299.5 }, rows, next, true), { from: 219.5, to: 299.5 });
  assert.deepEqual(restoreCandleRange(history, rows, rows.map((row) => ({ ...row })), false), history);
});

test("incremental updates cannot retain stale candles after a short or rolling history", () => {
  const data = candleData(rows, colors);
  assert.equal(canUpdateCandleData(data, data.map((bar, i) => i > 297 ? { ...bar, close: bar.close + .000001 } : bar)), true);
  assert.equal(canUpdateCandleData(data, [...data, { ...data.at(-1), time: data.at(-1).time + 60 }]), true);
  assert.equal(canUpdateCandleData(data.slice(0, 2), data.slice(1, 3)), false);
  assert.equal(canUpdateCandleData(data, [...data.slice(1), { ...data.at(-1), time: data.at(-1).time + 60 }]), false);
  assert.equal(canUpdateCandleData(data, [...data.slice(0, -1), { ...data.at(-1), time: data.at(-1).time + 60 }]), false);
  assert.equal(canUpdateCandleData(data, data.map((bar, i) => i === 100 ? { ...bar, close: bar.close + .000001 } : bar)), false);
});

test("range handles a short initial history, restored older bars, and expired windows", () => {
  assert.deepEqual(restoreCandleRange(null, [], rows.slice(0, 3), true), { from: -0.5, to: 2.5 });
  assert.deepEqual(constrainCandleRange({ from: 293, to: 294 }, 300), { from: 284, to: 294 });
  assert.deepEqual(constrainCandleRange({ from: -400, to: 500 }, 300), { from: -0.5, to: 299.5 });
  const range = { from: 99.5, to: 179.5 };
  assert.deepEqual(restoreCandleRange(range, rows.slice(10), rows, false), { from: 109.5, to: 189.5 });
  const later = rows.map((bar) => ({ ...bar, t: bar.t + 1000 * 60000 }));
  assert.deepEqual(restoreCandleRange(range, rows, later, false), { from: -0.5, to: 79.5 });
});

test("autoscale includes visible bands, moving lines and average while clamping remote orders", () => {
  const info = { priceRange: { minValue: 90, maxValue: 110 } };
  const overlay = { priceLines: [{ price: 1 }, { price: 10000 }], series: [{ values: rows.map(() => 102) }], bands: [{ upper: rows.map(() => 112), lower: rows.map(() => 88) }] };
  assert.deepEqual(overlayPriceRange(info, rows, overlay, { from: 219.5, to: 299.5 }).priceRange, { minValue: 60, maxValue: 140 });
  assert.deepEqual(overlayPriceRange(info, rows, { ...overlay, priceLines: [] }, { from: 219.5, to: 299.5 }).priceRange, { minValue: 88, maxValue: 112 });
  const values = rows.map((_, i) => i < 220 ? 500 : null);
  assert.deepEqual(overlayPriceRange(info, rows, { series: [{ values }] }, { from: 219.5, to: 299.5 }), info);
  assert.deepEqual(info.priceRange, { minValue: 90, maxValue: 110 });
});

test("axis tick precision retains sub-cent coins", () => {
  assert.equal(priceMinMove(61234.12), .01);
  assert.equal(priceMinMove(1.2345), .0001);
  assert.equal(priceMinMove(.02345), .00001);
  assert.equal(priceMinMove(.000234), .000001);
  assert.equal(priceMinMove(.00002345), .00000001);
});
