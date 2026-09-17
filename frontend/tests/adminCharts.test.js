import test from "node:test";
import assert from "node:assert/strict";
import {
  bucketHours, donutArcs, funnelLayout, gridValues, hBarLayout, nearestIndex, nearestSlot, niceStep, polylinePoints,
  seriesMax, stackTotals, tickEvery, tickIndexes, yScale,
} from "../src/lib/adminChartMath.js";

test("niceStep picks 1·2·5·10 × 10^n so four-ish gridlines fit", () => {
  assert.equal(niceStep(0), 0.5, "all-zero data still gets a 0.5 step (raw 0.25 → 5 × 0.1)");
  assert.equal(niceStep(1), 0.5);
  assert.equal(niceStep(10), 5);
  assert.equal(niceStep(30), 10);
  assert.equal(niceStep(318), 100);
  assert.equal(niceStep(1200), 500);
  assert.equal(niceStep(7.46), 2);
});

test("yScale tops out at a multiple of the step and never collapses to zero", () => {
  assert.deepEqual(yScale(318), { step: 100, yMax: 400 });
  assert.deepEqual(yScale(7.46), { step: 2, yMax: 8 });
  assert.deepEqual(yScale(0), { step: 0.5, yMax: 0.5 });
  assert.deepEqual(yScale(400), { step: 100, yMax: 400 });
  assert.equal(yScale(3.0000000000000004).yMax, 3, "floating noise does not add an extra gridline");
  assert.deepEqual(gridValues(8, 2), [0, 2, 4, 6, 8]);
  assert.deepEqual(gridValues(0.3, 0.1), [0, 0.1, 0.2, 0.3]);
});

test("yScale minStep: integer axes never get a fractional step, so rounded tick labels do not repeat", () => {
  assert.deepEqual(yScale(1, { minStep: 1 }), { step: 1, yMax: 1 });
  assert.deepEqual(yScale(2, { minStep: 1 }), { step: 1, yMax: 2 });
  assert.deepEqual(yScale(0, { minStep: 1 }), { step: 1, yMax: 1 }, "all-zero counts still draw one 0~1 cell");
  assert.deepEqual(gridValues(2, 1), [0, 1, 2]);
  assert.deepEqual(yScale(318, { minStep: 1 }), { step: 100, yMax: 400 }, "floor only matters when niceStep is below it");
  assert.deepEqual(yScale(1.2), { step: 0.5, yMax: 1.5 }, "fractional axes (pct, USD) keep the half step by default");
  assert.deepEqual(yScale(2, { minStep: 0 }), { step: 0.5, yMax: 2 });
  for (let max = 0; max <= 12; max += 1) {
    const { step, yMax } = yScale(max, { minStep: 1 });
    const labels = gridValues(yMax, step).map((v) => Math.round(v));
    assert.equal(new Set(labels).size, labels.length, `max=${max}: integer labels ${labels.join(",")} must be distinct`);
    assert.ok(yMax >= max, `max=${max}: top of axis covers the data`);
  }
});

test("date ticks: every day for 7, every 5 for 30, every 15 for 90, last index always present", () => {
  assert.equal(tickEvery(7), 1);
  assert.equal(tickEvery(30), 5);
  assert.equal(tickEvery(90), 15);
  assert.deepEqual(tickIndexes(7), [0, 1, 2, 3, 4, 5, 6]);
  assert.deepEqual(tickIndexes(30), [0, 5, 10, 15, 20, 25, 29]);
  assert.deepEqual(tickIndexes(90), [0, 15, 30, 45, 60, 75, 89]);
  assert.deepEqual(tickIndexes(31), [0, 5, 10, 15, 20, 25, 30]);
  assert.deepEqual(tickIndexes(27), [0, 5, 10, 15, 20, 26], "26 would sit 1 apart from 25 → 25 dropped");
  assert.deepEqual(tickIndexes(32), [0, 10, 20, 31], "32 days use a 10-day step; 31 replaces the too-close 30");
  assert.deepEqual(tickIndexes(0), []);
  assert.deepEqual(tickIndexes(1), [0]);
});

test("series helpers ignore non-numbers and treat missing points as 0", () => {
  assert.equal(seriesMax([{ data: [1, 5, null] }, { data: [2, "x", 4] }]), 5);
  assert.equal(seriesMax([]), 0);
  assert.deepEqual(stackTotals([{ data: [1, 2] }, { data: [3] }], 3), [4, 2, 0]);
  assert.equal(polylinePoints([0, 10], (i) => i * 100, (v) => 200 - v), "0.0,200.0 100.0,190.0");
});

test("pointer → index: nearest point for lines, containing slot for bars, clamped", () => {
  assert.equal(nearestIndex(60, 60, 900, 30), 0);
  assert.equal(nearestIndex(960, 60, 900, 30), 29);
  assert.equal(nearestIndex(60 + 450, 60, 900, 31), 15);
  assert.equal(nearestIndex(-500, 60, 900, 30), 0);
  assert.equal(nearestIndex(10, 60, 900, 1), 0);
  assert.equal(nearestSlot(60, 60, 900, 30), 0);
  assert.equal(nearestSlot(60 + 899, 60, 900, 30), 29);
  assert.equal(nearestSlot(5000, 60, 900, 30), 29);
  assert.equal(nearestSlot(100, 60, 900, 0), 0);
});

test("donut arcs: shares sum to 100, a single part draws a full ring, zero total draws nothing", () => {
  const { total, arcs } = donutArcs([{ label: "a", value: 3 }, { label: "b", value: 1 }], { cx: 90, cy: 90, r: 62 });
  assert.equal(total, 4);
  assert.equal(arcs.length, 2);
  assert.equal(arcs[0].share_pct, 75);
  assert.equal(arcs[1].share_pct, 25);
  assert.match(arcs[0].d, /^M90\.00 28\.00 A62 62 0 1 1 /, "75% sweeps more than half → large-arc flag 1");
  assert.match(arcs[1].d, /A62 62 0 0 1 /);
  const single = donutArcs([{ label: "only", value: 5 }, { label: "none", value: 0 }], { cx: 90, cy: 90, r: 62 });
  assert.equal((single.arcs[0].d.match(/A62 62/g) || []).length, 2, "two half arcs make a closed ring");
  assert.equal(single.arcs[1].d, "");
  assert.deepEqual(donutArcs([{ label: "x", value: 0 }], { cx: 0, cy: 0, r: 1 }), { total: 0, arcs: [] });
  assert.equal(donutArcs([{ label: "neg", value: -4 }, { label: "pos", value: 4 }], { cx: 0, cy: 0, r: 1 }).arcs[0].share_pct, 0);
});

test("bucketHours folds 24 hourly rows into 2-hour buckets with zero fill", () => {
  const buckets = bucketHours([{ hour: 0, items: 5, failures: 1 }, { hour: 1, items: 7, failures: 0 }, { hour: 23, items: 2, failures: 2 }, { hour: 99, items: 100 }]);
  assert.equal(buckets.length, 12);
  assert.deepEqual(buckets[0], { hour: 0, label: "00시", items: 12, failures: 1 });
  assert.deepEqual(buckets[11], { hour: 22, label: "22시", items: 2, failures: 2 });
  assert.deepEqual(buckets[5], { hour: 10, label: "10시", items: 0, failures: 0 });
  assert.equal(bucketHours(null).length, 12);
});

test("narrow layouts move labels above the bars instead of squeezing them", () => {
  assert.equal(hBarLayout(960).narrow, false);
  assert.equal(hBarLayout(960).L, 200);
  assert.equal(hBarLayout(390).narrow, true);
  assert.equal(hBarLayout(390).L, 0);
  assert.equal(funnelLayout(960).narrow, false);
  assert.equal(funnelLayout(960).L, 250);
  assert.equal(funnelLayout(390).narrow, true);
  assert.equal(funnelLayout(390).rowH, 58);
});
