import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/api.js";
import { createSymbolListResource, symbolListView } from "../src/lib/symbolListResource.js";

test("a domestic symbol list renews within one minute instead of keeping a five-minute copy", async (t) => {
  let clock = 1_000, calls = 0;
  t.mock.method(Date, "now", () => clock);
  t.mock.method(api, "symbols", async ({ exchange }) => ({ items: [{ symbol: ++calls === 1 ? "KRW-BTC" : "KRW-NEW", exchange }], fetched_at: clock / 1000 }));
  const catalogue = await import("../src/hooks/useSymbolList.js?native-ttl");
  t.after(catalogue.resetSymbolList);
  await catalogue.loadSymbolList(false, "upbit");
  clock += 59_999;
  assert.equal((await catalogue.loadSymbolList(false, "upbit")).items[0].symbol, "KRW-BTC");
  clock += 1;
  assert.equal((await catalogue.loadSymbolList(false, "upbit")).items[0].symbol, "KRW-NEW");
  assert.equal(calls, 2);
});

test("domestic stale catalogue retries after five seconds while preserving exchange isolation", async (t) => {
  let clock = 1_000;
  const calls = { upbit: 0, bithumb: 0 };
  t.mock.method(Date, "now", () => clock);
  t.mock.method(api, "symbols", async ({ exchange }) => ({ items: [{ symbol: `KRW-${exchange === "upbit" ? "BTC" : "ETH"}` }], fetched_at: clock / 1000, stale: ++calls[exchange] === 1 }));
  const catalogue = await import("../src/hooks/useSymbolList.js?native-retry");
  t.after(catalogue.resetSymbolList);
  assert.equal((await catalogue.loadSymbolList(false, "upbit")).items[0].symbol, "KRW-BTC");
  assert.equal((await catalogue.loadSymbolList(false, "bithumb")).items[0].symbol, "KRW-ETH");
  clock += 5_000;
  await catalogue.loadSymbolList(false, "upbit");
  assert.deepEqual(calls, { upbit: 2, bithumb: 1 });
});

test("Binance catalogue retains its existing five-minute lifetime", async (t) => {
  let clock = 1_000, calls = 0;
  t.mock.method(Date, "now", () => clock);
  t.mock.method(api, "symbols", async () => ({ items: [{ symbol: "BTCUSDT" }], fetched_at: ++calls }));
  const catalogue = await import("../src/hooks/useSymbolList.js?binance-ttl");
  t.after(catalogue.resetSymbolList);
  await catalogue.loadSymbolList(false, "binance");
  clock += 60_000;
  await catalogue.loadSymbolList(false, "binance");
  assert.equal(calls, 1);
  clock += 240_000;
  await catalogue.loadSymbolList(false, "binance");
  assert.equal(calls, 2);
});

test("stale responses preserve the source timestamp and expire five minutes after the last success", async () => {
  const source = 1_790_917_200_000;
  let clock = source;
  const catalogue = createSymbolListResource("upbit", async () => ({
    exchange: "upbit", items: [{ symbol: "KRW-BTC" }], fetched_at: source / 1000, stale: true,
  }), { now: () => clock, documentRef: null });
  await catalogue.refresh();
  clock += 60_000;
  await catalogue.refresh();
  const state = catalogue.getSnapshot();
  assert.equal(state.data.fetchedAt, source);
  assert.equal(symbolListView(state, "upbit", clock).stale, true);
  assert.equal(symbolListView(state, "upbit", source + 299_999).items[0].symbol, "KRW-BTC");
  const expired = symbolListView(state, "upbit", source + 300_000);
  assert.equal(expired.items, null);
  assert.match(expired.error, /오래/);
});

test("a failed refresh is not presented as a fresh catalogue and cannot extend its usable age", async () => {
  let clock = 1_790_917_200_000, fail = false;
  const catalogue = createSymbolListResource("bithumb", async () => {
    if (fail) throw Error("upstream unavailable");
    return { exchange: "bithumb", items: [{ symbol: "KRW-ETH" }], fetched_at: clock / 1000 };
  }, { now: () => clock, documentRef: null });
  await catalogue.refresh();
  clock += 60_000; fail = true;
  await assert.rejects(catalogue.refresh(), /upstream unavailable/);
  assert.equal(symbolListView(catalogue.getSnapshot(), "bithumb", clock).stale, true);
  clock += 240_000;
  assert.equal(symbolListView(catalogue.getSnapshot(), "bithumb", clock).items, null);
});

test("a response identifying a different exchange is rejected rather than publishing its native ticker list", async () => {
  const catalogue = createSymbolListResource("upbit", async () => ({ exchange: "bithumb", items: [{ symbol: "KRW-BTC" }] }), { documentRef: null });
  await assert.rejects(catalogue.refresh(), /다른 거래소/);
  assert.equal(catalogue.getSnapshot().data, null);
});

test("many consumers still share one refresh request and forced refresh revalidates HTTP caches", async () => {
  let calls = 0, release;
  const catalogue = createSymbolListResource("upbit", ({ exchange, cache }) => {
    calls += 1;
    assert.equal(exchange, "upbit");
    assert.equal(cache, "no-cache");
    return new Promise((resolve) => { release = resolve; });
  }, { documentRef: null });
  const first = catalogue.refresh(true), second = catalogue.refresh(true);
  await new Promise(setImmediate);
  assert.equal(calls, 1);
  release({ items: [{ symbol: "KRW-BTC" }], fetched_at: Date.now() / 1000 });
  assert.deepEqual(await first, await second);
});

test("empty catalogue responses use failure backoff instead of a successful five-second loop", async () => {
  let clock = 1_790_917_200_000;
  const delays = [];
  const catalogue = createSymbolListResource("upbit", async () => ({ items: [], fetched_at: clock / 1000 }), {
    now: () => clock, documentRef: null, setTimer: (_, delay) => { delays.push(delay); return delays.length; }, clearTimer: () => {},
  });
  const unsubscribe = catalogue.subscribe(() => {});
  for (let i = 0; i < 3; i++) {
    await assert.rejects(catalogue.refresh(), /종목 목록/);
    assert.equal(catalogue.getSnapshot().data, null);
    assert.equal(delays.at(-1), 5_000 * 2 ** i);
    clock += delays.at(-1);
  }
  unsubscribe();
});

test("a stale domestic response without a valid source time cannot renew the last good list", async () => {
  const source = 1_790_917_200_000;
  let clock = source, malformed = false;
  const catalogue = createSymbolListResource("bithumb", async () => ({
    items: [{ symbol: "KRW-ETH" }], fetched_at: malformed ? undefined : source / 1000, stale: malformed,
  }), { now: () => clock, documentRef: null });
  await catalogue.refresh();
  malformed = true; clock += 60_000;
  await assert.rejects(catalogue.refresh(), /확인 시각/);
  assert.equal(catalogue.getSnapshot().data.fetchedAt, source);
  assert.equal(symbolListView(catalogue.getSnapshot(), "bithumb", source + 300_000).items, null);
});
