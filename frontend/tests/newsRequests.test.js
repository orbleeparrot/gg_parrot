import assert from "node:assert/strict";
import test from "node:test";
import { createNewsBriefingQueue, prepareNewsResponse } from "../src/lib/newsBriefings.js";

const flush = () => new Promise(setImmediate);
const ready = { items: [{ id: "a", title: "비트코인 ETF 자금 유입 증가" }], translation: { status: "ready", pending_count: 0 } };
const pending = { items: [], translation: { status: "partial", pending_count: 2, retry_after_seconds: 30 } };

function harness(load, keys = ["BTC"]) {
  let clock = 1000;
  let nextId = 0;
  const timers = new Map();
  const states = new Map();
  const queue = createNewsBriefingQueue({
    keys, load, onChange: (key, state) => states.set(key, state), now: () => clock,
    setTimer: (fn, delay) => { timers.set(++nextId, { fn, delay }); return nextId; },
    clearTimer: (id) => timers.delete(id),
  });
  return {
    queue, states,
    delays: () => [...timers.values()].map((timer) => timer.delay),
    async next() {
      const [id, timer] = timers.entries().next().value || [];
      assert.ok(timer, "expected a scheduled request");
      timers.delete(id);
      clock += timer.delay;
      timer.fn();
      await flush();
    },
  };
}

test("only Korean headlines are displayed while old API English responses remain pending", () => {
  const payload = prepareNewsResponse({
    items: [...ready.items, { title: "Bitcoin ETF inflows rise" }], overview: "English overview",
    translation: { status: "partial", pending_count: 1 },
  });
  assert.deepEqual(payload.items, ready.items);
  assert.equal(payload.overview, null);
  assert.equal(payload.translation.pending_count, 1);
  assert.equal(payload.translation.status, "partial");
  assert.equal(prepareNewsResponse({ items: [{ title: "English" }] }).translation.status, "partial");
  assert.equal(prepareNewsResponse({ items: [] }).translation.status, "ready");
  assert.equal(prepareNewsResponse(pending).translation.pending_count, 2);
});

test("market and coin translations automatically refresh only pending keys and stop when ready", async () => {
  const calls = [];
  const h = harness(async (key) => {
    calls.push(key);
    return key === "market" && calls.filter((value) => value === key).length === 1 ? pending : ready;
  }, ["market", "BTC", "BTC"]);
  h.queue.start();
  await h.next();
  assert.deepEqual(calls, ["market", "BTC"]);
  assert.equal(h.states.get("market").data.items.length, 0);
  assert.equal(h.states.get("market").data.translation.status, "partial");
  assert.deepEqual(h.delays(), [30000]);
  await h.next();
  assert.deepEqual(calls, ["market", "BTC", "market"]);
  assert.equal(h.states.get("market").data.items.length, 1);
  assert.deepEqual(h.delays(), []);
  h.queue.stop();
});

test("slow source requests are limited to two concurrent keys with no overlapping retry", async () => {
  const releases = new Map();
  const calls = [];
  let active = 0;
  let peak = 0;
  const h = harness((key) => {
    calls.push(key); active += 1; peak = Math.max(peak, active);
    return new Promise((resolve) => releases.set(key, () => { active -= 1; resolve(ready); }));
  }, ["BTC", "ETH", "SOL"]);
  h.queue.start();
  await h.next();
  assert.deepEqual(calls, ["BTC", "ETH"]);
  h.queue.retry("BTC");
  assert.deepEqual(h.delays(), []);
  releases.get("BTC")();
  await flush();
  assert.deepEqual(calls, ["BTC", "ETH", "SOL"]);
  releases.get("ETH")(); releases.get("SOL")();
  await flush();
  assert.equal(peak, 2);
  assert.deepEqual(h.delays(), []);
  h.queue.stop();
});

test("translation retries respect server cooldown and back off instead of looping quickly", async () => {
  const h = harness(async () => ({ ...pending, translation: { ...pending.translation, retry_after_seconds: 45 } }));
  h.queue.start();
  for (const delay of [45000, 60000, 120000, 240000, 300000, 300000]) {
    await h.next();
    assert.deepEqual(h.delays(), [delay]);
  }
  h.queue.stop();
  assert.deepEqual(h.delays(), []);
});

test("busy responses keep retrying at a bounded cadence until translation recovers", async () => {
  let fail = true;
  const h = harness(async () => {
    if (fail) throw Object.assign(new Error("잠시 후 다시 시도해 주세요"), { status: 429 });
    return ready;
  });
  h.queue.start();
  for (const delay of [30000, 60000, 120000, 240000, 300000, 300000]) {
    await h.next();
    assert.deepEqual(h.delays(), [delay]);
  }
  assert.equal(h.states.get("BTC").status, "error");
  fail = false;
  await h.next();
  assert.equal(h.states.get("BTC").data.translation.status, "ready");
  assert.deepEqual(h.delays(), []);
  h.queue.stop();
});

test("pending headlines survive repeated temporary failures while permanent client errors stop", async () => {
  let calls = 0;
  const h = harness(async () => {
    calls += 1;
    if (calls === 1) return pending;
    throw Object.assign(new Error("요청 실패"), { status: calls <= 7 ? 503 : 403 });
  });
  h.queue.start();
  for (let attempt = 0; attempt < 7; attempt += 1) await h.next();
  assert.equal(h.states.get("BTC").data.translation.status, "partial");
  assert.deepEqual(h.delays(), [300000]);
  await h.next();
  assert.deepEqual(h.delays(), []);
  assert.equal(h.states.get("BTC").status, "error");
  h.queue.stop();
});

test("hiding the page aborts pending requests and resuming waits for the existing cooldown", async () => {
  let receivedSignal;
  let attempts = 0;
  const h = harness((_key, signal) => {
    receivedSignal = signal;
    attempts += 1;
    if (attempts > 1) return Promise.resolve(pending);
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => {
      reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    }));
  });
  h.queue.start();
  await h.next();
  h.queue.setVisible(false);
  assert.equal(receivedSignal.aborted, true);
  await flush();
  assert.deepEqual(h.delays(), []);
  h.queue.setVisible(true);
  await h.next();
  assert.equal(attempts, 2);
  h.queue.setVisible(false);
  h.queue.setVisible(true);
  await h.next();
  assert.equal(attempts, 2);
  assert.deepEqual(h.delays(), [30000]);
  h.queue.stop();
});

test("leaving a page discards late results and a fresh visit fetches the news again", async () => {
  let release;
  const h = harness(() => new Promise((resolve) => { release = resolve; }));
  h.queue.start();
  await h.next();
  const before = h.states.get("BTC");
  h.queue.stop();
  release(ready);
  await flush();
  assert.equal(h.states.get("BTC"), before);
  assert.deepEqual(h.delays(), []);
  let calls = 0;
  const revisit = harness(async () => { calls += 1; return ready; });
  revisit.queue.start();
  await revisit.next();
  assert.equal(calls, 1);
  assert.equal(revisit.states.get("BTC").data.translation.status, "ready");
  revisit.queue.stop();
});
