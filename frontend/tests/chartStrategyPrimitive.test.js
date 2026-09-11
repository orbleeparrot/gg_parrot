import test from "node:test";
import assert from "node:assert/strict";
import { StrategyPrimitive, resolveChartColor } from "../src/lib/chartStrategyPrimitive.js";
import { computeStrategyOverlay, computeSessionOverlay } from "../src/lib/indicators.js";

const candles = Array.from({ length: 8 }, (_, index) => ({
  t: 1767225600000 + index * 60000, o: 99, h: 110, l: 90, c: 101, closed: true,
}));
const palette = { up: "green", down: "red", surface: "black", rsi: "indigo" };

function harness({ rows = candles, overlay = null, kind = "price", colors = palette, range = { from: 0, to: 7 }, missing = false } = {}) {
  const operations = [];
  const times = [];
  let path = [];
  let dash = [];
  const context = {
    save() {}, restore() {}, rect() {}, clip() {},
    beginPath() { path = []; },
    moveTo(x, y) { path.push(["M", x, y]); },
    lineTo(x, y) { path.push(["L", x, y]); },
    closePath() { path.push(["Z"]); },
    setLineDash(value) { dash = value; },
    stroke() { operations.push({ type: "stroke", path: [...path], color: this.strokeStyle, width: this.lineWidth, dash, alpha: this.globalAlpha }); },
    fill() { operations.push({ type: "fill", path: [...path], color: this.fillStyle }); },
    fillRect(x, y, width, height) { operations.push({ type: "rect", x, y, width, height, color: this.fillStyle }); },
    strokeText() {},
    fillText(label, x, y) { operations.push({ type: "label", label, x, y, color: this.fillStyle }); },
  };
  const chart = { timeScale: () => ({
    getVisibleLogicalRange: () => range,
    timeToCoordinate(time) {
      times.push(time);
      return missing ? null : 20 + (time - rows[0].t / 1000) / 3;
    },
  }) };
  const series = { priceToCoordinate: (price) => missing ? null : 200 - price };
  let updates = 0;
  const primitive = new StrategyPrimitive({ candles: rows, overlay, colors, kind });
  primitive.attached({ chart, series, requestUpdate: () => updates++ });
  const draw = (layer = "normal") => primitive.paneViews().find((view) => view.zOrder() === layer).renderer().draw({
    useMediaCoordinateSpace: (callback) => callback({ context, mediaSize: { width: 240, height: 220 } }),
  });
  return { primitive, draw, operations, times, updates: () => updates };
}

test("indicator lines preserve warm-up and internal gaps, exact weights, and dash lengths", () => {
  const h = harness({ overlay: { series: [{
    values: [null, 101, 102, null, 104, 105, Infinity, 107], color: "orange", width: 1.5, dash: "4 3",
  }] } });
  h.draw();
  const line = h.operations.find((op) => op.type === "stroke");
  assert.deepEqual(line.path, [["M", 40, 99], ["L", 60, 98], ["M", 100, 96], ["L", 120, 95], ["M", 160, 93]]);
  assert.equal(line.width, 1.5);
  assert.deepEqual(line.dash, [4, 3]);
  assert.equal(line.alpha, .95);
  assert.ok(h.times.every((time) => time >= candles[0].t / 1000 && time <= candles.at(-1).t / 1000));
});

test("band shading stays beneath candles and cannot bridge missing indicator data", () => {
  const band = { upper: [101, 102, null, 104, 105], lower: [91, 92, null, 94, 95], fill: "rgba(99,102,241,0.08)" };
  const h = harness({ overlay: { bands: [band] } });
  h.draw("bottom");
  assert.deepEqual(h.operations.map((op) => op.path), [
    [["M", 20, 99], ["L", 40, 98], ["L", 40, 108], ["L", 20, 109], ["Z"]],
    [["M", 80, 96], ["L", 100, 95], ["L", 100, 105], ["L", 80, 106], ["Z"]],
  ]);
  assert.deepEqual(band.lower, [91, 92, null, 94, 95]);
  h.operations.length = 0;
  h.draw("normal");
  assert.equal(h.operations.length, 0);
});

test("a short entry is a sell triangle above the high; short exit is a buy below the low", () => {
  const h = harness({ overlay: { markers: [
    { index: 2, kind: "entry", side: "sell", label: "숏 진입" },
    { index: 4, kind: "exit", side: "buy", label: "숏 청산" },
  ] } });
  h.draw();
  const [sell, buy] = h.operations.filter((op) => op.type === "fill");
  assert.equal(sell.color, "red");
  assert.deepEqual(sell.path, [["M", 60, 88], ["L", 64, 81], ["L", 56, 81], ["Z"]]);
  assert.equal(buy.color, "green");
  assert.deepEqual(buy.path, [["M", 100, 112], ["L", 104, 119], ["L", 96, 119], ["Z"]]);
});

test("price lines keep numeric values and distinct labels, with no off-scale fake price label", () => {
  const h = harness({ overlay: { priceLines: [
    { price: 100, color: "rgb(var(--chart-up))", dash: "2 4", label: "매수 100" },
    { price: 101, color: "indigo", dash: "5 3", label: "내 평단 101" },
    { price: 10000, color: "red", label: "매도 10,000" },
  ] } });
  h.draw();
  const lines = h.operations.filter((op) => op.type === "stroke");
  assert.deepEqual(lines.map((line) => line.path[0]), [["M", 0, 100], ["M", 0, 99]]);
  assert.deepEqual(lines.map((line) => line.dash), [[2, 4], [5, 3]]);
  assert.equal(lines[0].color, "green");
  const labels = h.operations.filter((op) => op.type === "label");
  assert.deepEqual(labels.map((label) => label.label).sort(), ["내 평단 101", "매수 100"]);
  assert.ok(Math.abs(labels[0].y - labels[1].y) >= 11);
  assert.equal(h.primitive.autoscaleInfo(), null);
});

test("RSI retains both zones, dashed thresholds, curve, and the short-side action labels", () => {
  const h = harness({ kind: "rsi", overlay: { rsi: {
    values: [null, null, 45, 65, 80], entry: 30, exit: 70, highLabel: "숏 진입(매도)", lowLabel: "숏 청산(매수)",
  } } });
  h.draw("bottom");
  assert.equal(h.operations.filter((op) => op.type === "rect").length, 2);
  h.draw();
  const lines = h.operations.filter((op) => op.type === "stroke");
  assert.deepEqual(lines.slice(0, 2).map((line) => line.dash), [[4, 3], [4, 3]]);
  assert.equal(lines[2].color, "indigo");
  assert.deepEqual(h.operations.filter((op) => op.type === "label").map((op) => op.label), [
    "과매수 70 · 숏 진입(매도)", "과매도 30 · 숏 청산(매수)",
  ]);
});

test("null price coordinates and detached lifecycle never turn into price-zero drawings", () => {
  const h = harness({ missing: true, overlay: {
    priceLines: [{ price: 100, color: "orange", label: "내 평단 100" }],
    series: [{ values: [30, 40], color: "orange" }], markers: [{ index: 1, side: "buy" }],
  } });
  h.draw("bottom");
  h.draw();
  assert.ok(h.operations.every((op) => op.type === "stroke" && op.path.length === 0));
  h.operations.length = 0;
  h.primitive.detached();
  h.draw();
  assert.equal(h.operations.length, 0);
});

test("all-null RSI warm-up keeps its fixed zones and labels without drawing an invented RSI value", () => {
  const values = candles.map(() => null);
  const h = harness({ missing: true, kind: "rsi", overlay: { values, entry: 30, exit: 70 } });
  h.draw("bottom");
  h.draw();
  const zones = h.operations.filter((op) => op.type === "rect");
  assert.equal(zones.length, 2);
  const expectedUpper = 220 - 1 - 220 * .08 - (220 * .84 - 1) * .7;
  const expectedLower = 220 - 1 - 220 * .08 - (220 * .84 - 1) * .3;
  assert.ok(Math.abs(zones[0].height - expectedUpper) < 1e-10);
  assert.ok(Math.abs(zones[1].y - expectedLower) < 1e-10);
  const lines = h.operations.filter((op) => op.type === "stroke" && op.path.length > 0);
  assert.equal(lines.length, 2, "only threshold lines; no fabricated RSI curve");
  assert.deepEqual(h.operations.filter((op) => op.type === "label").map((op) => op.label), ["과매수 70", "과매도 30"]);
  assert.ok(values.every((value) => value === null));
});

test("theme resolution runs during updates, not during each canvas frame", () => {
  const resolved = [];
  const h = harness({ overlay: {
    series: [{ values: [1, 2], color: "custom" }], priceLines: [{ price: 100, color: "custom" }],
  }, colors: { ...palette, resolve: (color) => { resolved.push(color); return "purple"; } } });
  h.draw(); h.draw(); h.draw("bottom");
  assert.deepEqual(resolved, ["custom"]);
  const previousUpdates = h.updates();
  h.primitive.setData({ colors: { ...palette, resolve: () => "orange" } });
  h.draw();
  assert.equal(h.updates(), previousUpdates + 1);
  assert.equal(h.operations.at(-1).color, "orange");
  assert.equal(resolveChartColor("rgb(var(--chart-up) / .8)", { ownerDocument: { defaultView: {
    getComputedStyle: () => ({ getPropertyValue: () => " 14 203 129 " }),
  } } }), "rgb(14 203 129 / .8)");
});

test("all A–K overlay and session specifications pass through drawing without mutation", () => {
  const forms = {
    A: { take_profit_pct: 5, use_stop_loss: true, stop_loss_pct: 3 },
    B: { buy_price: 96, sell_price: 106 }, C: {},
    D: { lower_price: 90, upper_price: 110, grid_count: 8, grid_mode: "geometric" },
    E: { trail_percent: 3, entry_mode: "dip", entry_dip: 2 },
    F: { rsi_period: 7, entry_threshold: 35, exit_threshold: 65 },
    G: { bb_period: 12, bb_std: 1.3, strategy: "reversion", exit_target: "opposite" },
    H: { price_deviation: 1, safety_order_step_scale: 1.4, max_safety_orders: 4, take_profit: 3 },
    I: { k: .4 }, J: { fast_period: 5, slow_period: 16, ma_type: "EMA" },
    K: { drop_trigger_pct: 3, long_take_profit_pct: 5, short_take_profit_pct: 4, short_stop_loss_pct: 2 },
  };
  const rows = Array.from({ length: 160 }, (_, index) => {
    const o = 100 + Math.sin((index - 1) / 4) * 6, c = 100 + Math.sin(index / 4) * 6;
    return { t: candles[0].t + index * 60000, o, c, h: Math.max(o, c) + 1, l: Math.min(o, c) - 1, closed: true };
  });
  for (const [rule_type, params] of Object.entries(forms)) {
    for (const position_side of ["long", "short"]) {
      for (const session of [false, true]) {
        const overlay = session
          ? computeSessionOverlay({ rule_type, position_side, params }, 100, position_side, rows)
          : computeStrategyOverlay({ rule_type, position_side, ...params }, rows);
        const before = JSON.stringify(overlay);
        for (const kind of ["price", ...(overlay?.rsi ? ["rsi"] : [])]) {
          const h = harness({ rows, overlay, kind, range: { from: 70, to: 159 } });
          h.draw("bottom"); h.draw();
          assert.ok(h.operations.filter((op) => op.path).every((op) => op.path.every((point) => point.slice(1).every(Number.isFinite))));
        }
        assert.equal(JSON.stringify(overlay), before, `${rule_type} ${position_side} session=${session}`);
      }
    }
  }
});
