import test from "node:test";
import assert from "node:assert/strict";
import { resolveChartMarket, chartTimeKst, chartDateKst, chartClockKst, chartResponseMeta, isChartFresh, isChartLive, createChartStream, applyChartHistory, applyChartLive } from "../src/lib/chartSource.js";

const NOW = Date.parse("2026-10-01T00:00:00+09:00");
const request = { exchange: "binance", symbol: "BTCUSDT", interval: "1m", market: "futures" };
const bar = (t, c) => ({ t, o: c, h: c, l: c, c, v: 1, closed: false });
const payload = (market, candles, extra = {}) => ({ ...request, market, candles, fetched_at_ms: NOW, server_time: NOW, cache_age_ms: 0, stale: false, ...extra });
const legacy = (market, candles, extra = {}) => ({ ...request, market, candles, server_time: NOW, ...extra });

test("guide market resolution follows explicit market, short/leverage/SAR and native spot", () => {
  assert.equal(resolveChartMarket({}), "spot");
  assert.equal(resolveChartMarket({ exchange: "binance", rule_type: "K", market: "auto" }), "futures");
  assert.equal(resolveChartMarket({ position_side: "short", leverage: 1 }), "futures");
  assert.equal(resolveChartMarket({ leverage: 3 }), "futures");
  assert.equal(resolveChartMarket({ market: "spot", position_side: "short" }), "spot");
  assert.equal(resolveChartMarket({ market: "futures", leverage: 1 }), "futures");
  assert.equal(resolveChartMarket({ exchange: "upbit", market: "auto" }), "spot");
  assert.equal(resolveChartMarket({ exchange: "bithumb", leverage: 1 }), "spot");
});

test("all chart time labels use KST without modifying original UTC timestamps", () => {
  assert.equal(chartTimeKst(NOW), "10/1 00:00");
  assert.equal(chartDateKst(NOW), "10/1");
  assert.equal(chartClockKst(NOW), "00:00");
  assert.equal(chartTimeKst(NOW - 1000), "9/30 23:59");
  const stream = applyChartHistory(createChartStream(request), payload("futures", [bar(NOW, 1)]), NOW);
  assert.equal(stream.candles[0].t, NOW);
});

test("response provenance uses actual market and refuses another venue, ticker or interval", () => {
  const meta = chartResponseMeta(payload("spot", []), request, NOW);
  assert.equal(meta.market, "spot");
  assert.equal(meta.requestedMarket, "futures");
  assert.equal(meta.fallback, true);
  for (const wrong of [{ exchange: "upbit" }, { symbol: "ETHUSDT" }, { interval: "1d" }, { market: "other" }]) {
    assert.throws(() => chartResponseMeta(payload("spot", [], wrong), request, NOW));
  }
});

test("freshness relies on origin fetch/cache age, not a fresh served time or open flag", () => {
  assert.equal(isChartFresh(chartResponseMeta(payload("futures", []), request, NOW), NOW), true);
  const old = chartResponseMeta(payload("futures", [], { fetched_at_ms: NOW - 60000, server_time: NOW, cache_age_ms: 60000 }), request, NOW);
  assert.equal(isChartFresh(old, NOW), false);
  assert.equal(isChartFresh(chartResponseMeta(payload("futures", [], { stale: true }), request, NOW), NOW), false);
  assert.equal(isChartFresh(chartResponseMeta(payload("futures", [], { awaiting_candle_refresh: true }), request, NOW), NOW), false);
  const recent = chartResponseMeta(payload("futures", []), request, NOW);
  assert.equal(isChartFresh(recent, NOW + 16000), false);
  assert.equal(isChartFresh(chartResponseMeta({ market: "futures" }, request, NOW), NOW), false);
  assert.equal(isChartFresh(chartResponseMeta({ market: "futures", server_time: NOW }, request, NOW), NOW), false);
});

test("only a confirmed-fresh not-yet-ended open candle may show LIVE", () => {
  const meta = chartResponseMeta(payload("futures", []), request, NOW);
  assert.equal(isChartLive(meta, bar(NOW, 1), NOW), true);
  assert.equal(isChartLive(meta, bar(NOW - 60000, 1), NOW), false);
  assert.equal(isChartLive(meta, { ...bar(NOW, 1), closed: true }, NOW), false);
  assert.equal(isChartLive(meta, bar(NOW + 60000, 1), NOW), false);
  assert.equal(isChartLive(meta, bar(NOW, 1), NOW + 16000), false);
});

test("history fallback fixes live source to spot and rejects mixed futures candles", () => {
  const stream = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW - 60000, 10), bar(NOW, 11)]), NOW);
  assert.equal(stream.source.market, "spot");
  const mismatch = applyChartLive(stream, payload("futures", [bar(NOW, 900)]), stream.revision, NOW);
  assert.equal(mismatch.reload, true);
  assert.deepEqual(mismatch.stream.candles.map((row) => row.c), [10, 11]);
  const accepted = applyChartLive(stream, payload("spot", [bar(NOW, 12)]), stream.revision, NOW);
  assert.equal(accepted.reload, false);
  assert.deepEqual(accepted.stream.candles.map((row) => row.c), [10, 12]);
});

test("old-source in-flight response cannot overwrite a reloaded full history", () => {
  const initial = applyChartHistory(createChartStream(request), payload("futures", [bar(NOW, 90)]), NOW);
  const replaced = applyChartHistory(initial, payload("spot", [bar(NOW, 10)]), NOW);
  const late = applyChartLive(replaced, payload("futures", [bar(NOW, 900)]), initial.revision, NOW);
  assert.equal(late.ignored, true);
  assert.equal(late.reload, false);
  assert.deepEqual(late.stream.candles.map((row) => row.c), [10]);
});

test("same-source history refresh must not retire a concurrent fresher live request", () => {
  const initial = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW, 10)]), NOW);
  const refreshed = applyChartHistory(initial, payload("spot", [bar(NOW, 11)], { fetched_at_ms: NOW + 1000 }), NOW + 1000);
  const concurrent = applyChartLive(refreshed, payload("spot", [bar(NOW, 20)], { fetched_at_ms: NOW + 2000 }), initial.revision, NOW + 2000);
  assert.equal(concurrent.ignored, false);
  assert.equal(concurrent.stream.candles.at(-1).c, 20);
});

test("an older cached history does not undo newer same-source live prices", () => {
  let stream = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW - 60000, 10), bar(NOW, 11)]), NOW);
  stream = applyChartLive(stream, payload("spot", [bar(NOW, 12)], { fetched_at_ms: NOW + 1000 }), stream.revision, NOW + 1000).stream;
  const refreshed = applyChartHistory(stream, payload("spot", [bar(NOW - 60000, 10), bar(NOW, 11)]), NOW + 2000);
  assert.deepEqual(refreshed.candles.map((row) => row.c), [10, 12]);
});

test("stale live payload is reported but never regresses current candle values", () => {
  const stream = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW, 12)]), NOW);
  const stale = applyChartLive(stream, payload("spot", [bar(NOW, 9)], { fetched_at_ms: NOW - 60000, cache_age_ms: 60000, stale: true }), stream.revision, NOW);
  assert.deepEqual(stale.stream.candles.map((row) => row.c), [12]);
  assert.equal(stale.stream.live.stale, true);
  assert.equal(stale.ignored, true);
});

test("stale channel status cannot erase accepted live provenance before an older history arrives", () => {
  let stream = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW, 10)]), NOW);
  stream = applyChartLive(stream, payload("spot", [bar(NOW, 20)], { fetched_at_ms: NOW + 2000 }), stream.revision, NOW + 2000).stream;
  stream = applyChartLive(stream, payload("spot", [bar(NOW, 1)], { fetched_at_ms: NOW - 1000, stale: true }), stream.revision, NOW + 3000).stream;
  stream = applyChartHistory(stream, payload("spot", [bar(NOW, 15)], { fetched_at_ms: NOW + 1000 }), NOW + 4000);
  assert.equal(stream.candles.at(-1).c, 20);
  assert.equal(stream.live.stale, true);
});

test("a confirmed live recovery requests full history after expired unconfirmed history", () => {
  const stream = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW, 10)], { awaiting_candle_refresh: true }), NOW);
  const recovered = applyChartLive(stream, payload("spot", [bar(NOW, 20)], { fetched_at_ms: NOW + 1000 }), stream.revision, NOW + 1000);
  assert.equal(recovered.reload, true);
  assert.equal(recovered.stream.candles.at(-1).c, 20);
});

test("same KRW ticker on two exchanges cannot mix through live data", () => {
  const domestic = { exchange: "upbit", symbol: "KRW-BTC", interval: "1d", market: "spot" };
  const data = { ...domestic, candles: [bar(NOW, 100000)], fetched_at_ms: NOW, cache_age_ms: 0 };
  const stream = applyChartHistory(createChartStream(domestic), data, NOW);
  assert.throws(() => applyChartLive(stream, { ...data, exchange: "bithumb", candles: [bar(NOW, 200000)] }, stream.revision, NOW));
  assert.equal(stream.candles[0].c, 100000);
});

test("legacy live prices may move without claiming an origin fetch timestamp or LIVE", () => {
  const stream = applyChartHistory(createChartStream(request), legacy("spot", [bar(NOW, 10)]), NOW);
  const accepted = applyChartLive(stream, legacy("spot", [bar(NOW, 20)], { server_time: NOW + 1000 }), stream.revision, NOW + 1000);
  assert.equal(accepted.ignored, false);
  assert.equal(accepted.stream.candles.at(-1).c, 20);
  assert.equal(accepted.stream.live.fetchedAt, 0);
  assert.equal(accepted.stream.live.priceOrigin, NOW + 1000);
  assert.equal(isChartFresh(accepted.stream.live, NOW + 1000), false);
  assert.equal(isChartLive(accepted.stream.live, accepted.stream.candles.at(-1), NOW + 1000), false);
});

test("legacy live only accepts explicit matching identity and a bounded real server timestamp", () => {
  const stream = applyChartHistory(createChartStream(request), legacy("spot", [bar(NOW, 10)]), NOW);
  for (const extra of [
    { server_time: NOW - 15001 }, { server_time: NOW + 60000 }, { server_time: 0 }, { server_time: "invalid" },
    { stale: true }, { awaiting_candle_refresh: true }, { fetched_at_ms: 0 },
    { exchange: undefined }, { symbol: undefined }, { interval: undefined }, { market: undefined },
  ]) {
    const rejected = applyChartLive(stream, legacy("spot", [bar(NOW, 20)], extra), stream.revision, NOW);
    assert.equal(rejected.ignored, true, JSON.stringify(extra));
    assert.equal(rejected.stream.candles.at(-1).c, 10);
  }
  const mismatch = applyChartLive(stream, legacy("futures", [bar(NOW, 900)]), stream.revision, NOW);
  assert.equal(mismatch.ignored, true);
  assert.equal(mismatch.reload, true);
});

test("accepted legacy live provenance protects against old history and live responses", () => {
  let stream = applyChartHistory(createChartStream(request), legacy("spot", [bar(NOW, 10)]), NOW);
  stream = applyChartLive(stream, legacy("spot", [bar(NOW, 20)], { server_time: NOW + 2000 }), stream.revision, NOW + 2000).stream;
  stream = applyChartHistory(stream, legacy("spot", [bar(NOW, 11)], { server_time: NOW + 1000 }), NOW + 3000);
  assert.equal(stream.candles.at(-1).c, 20);
  const old = applyChartLive(stream, legacy("spot", [bar(NOW, 12)], { server_time: NOW + 1000 }), stream.revision, NOW + 3000);
  assert.equal(old.ignored, true);
  assert.equal(old.stream.candles.at(-1).c, 20);
});

test("older legacy live cannot overwrite newer native origin metadata", () => {
  const stream = applyChartHistory(createChartStream(request), payload("spot", [bar(NOW, 30)], { fetched_at_ms: NOW + 2000 }), NOW + 3000);
  const old = applyChartLive(stream, legacy("spot", [bar(NOW, 20)], { server_time: NOW + 1000 }), stream.revision, NOW + 3000);
  assert.equal(old.ignored, true);
  assert.equal(old.stream.candles.at(-1).c, 30);
});
