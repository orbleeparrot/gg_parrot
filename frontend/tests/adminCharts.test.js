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

test("funnelRows: bar width never exceeds innerW, a step larger than the first (mismatched sets) gets no ratio", async () => {
  const { funnelRows } = await import("../src/lib/adminChartMath.js");
  // 방문 5 · 가입 432 — 옛 퍼널이 8,640% 를 그리며 막대가 화면 밖으로 나가던 자료.
  const rows = funnelRows([{ key: "visit", label: "방문", count: 5 }, { key: "signup", label: "가입", count: 432 }], 600);
  assert.equal(rows[0].width, 600);
  assert.equal(rows[0].pctFirst, 100);
  assert.equal(rows[0].pctPrev, null, "first step has no previous");
  assert.equal(rows[1].width, 600, "clamped to innerW instead of 51,840px");
  assert.equal(rows[1].pctFirst, null, "count > first → —");
  assert.equal(rows[1].pctPrev, null);

  const ok = funnelRows([{ label: "a", count: 100 }, { label: "b", count: 25 }, { label: "c", count: 0 }], 400);
  assert.equal(ok[1].width, 100);
  assert.equal(ok[1].pctFirst, 25);
  assert.equal(ok[1].pctPrev, 25);
  assert.equal(ok[2].width, 4, "zero still draws a sliver so the row is visible");
  assert.equal(ok[2].pctFirst, 0);
  assert.equal(ok[2].pctPrev, 0);
  assert.equal(ok[2].label, "c");

  // 서버가 pct 키를 준 행은 그 값이 진실 — null 은 "측정 불가"라 여기서 나눠 지어내지 않는다.
  const server = funnelRows([
    { key: "visit", count: 10, pct_of_first: 100, pct_of_prev: null },
    { key: "builder", count: 4, pct_of_first: null, pct_of_prev: null },
    { key: "backtest", count: 3, pct_of_first: 30, pct_of_prev: 75 },
  ], 200);
  assert.equal(server[1].pctFirst, null);
  assert.equal(server[1].pctPrev, null);
  assert.equal(server[2].pctFirst, 30);
  assert.equal(server[2].pctPrev, 75);
  assert.equal(server[1].label, "builder", "label falls back to the key");
  assert.deepEqual(funnelRows(null, 100), []);
  assert.equal(funnelRows([{ count: 0 }], 100)[0].width, 4, "first = 0 never divides by zero");
});

test("polylineSegments breaks the line at null (pre-coverage days are gaps, not zeros)", async () => {
  const { lastFiniteIndex, polylineSegments } = await import("../src/lib/adminChartMath.js");
  const x = (i) => i * 10;
  const y = (v) => 100 - v;
  const segs = polylineSegments([null, null, 5, 7, null, 2, null], x, y);
  assert.equal(segs.length, 2);
  assert.equal(segs[0].points, "20.0,95.0 30.0,93.0");
  assert.equal(segs[0].single, false);
  assert.deepEqual(segs[0].first, { i: 2, v: 5 });
  assert.deepEqual(segs[0].last, { i: 3, v: 7 });
  assert.equal(segs[1].single, true, "a lone point gets a dot instead of an invisible zero-length line");
  assert.deepEqual(segs[1].first, { i: 5, v: 2 });
  assert.deepEqual(polylineSegments([null, undefined, "x"], x, y), []);
  assert.equal(polylineSegments([0, 0], x, y)[0].points, "0.0,100.0 10.0,100.0", "real zeros still draw");
  assert.equal(lastFiniteIndex([1, 2, null, null]), 1);
  assert.equal(lastFiniteIndex([null]), -1);
  assert.equal(lastFiniteIndex([]), -1);
  assert.equal(lastFiniteIndex([null, 0]), 1, "0 is a value");
});
