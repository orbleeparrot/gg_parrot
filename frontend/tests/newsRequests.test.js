import assert from "node:assert/strict";
import test from "node:test";
import * as libs from "../src/lib/newsBriefings.js";
const { createNewsBriefingQueue, historicalNewsLabel, prepareNewsResponse } = libs;

const flush = () => new Promise(setImmediate);
const ready = { items: [{ id: "a", title: "비트코인 ETF 자금 유입 증가" }], translation: { status: "ready", pending_count: 0 } };
const pending = { items: [], translation: { status: "partial", pending_count: 2, retry_after_seconds: 30 } };

test("archived coin news shows its actual KST publication date without relabeling recent articles", () => {
  assert.equal(historicalNewsLabel({ is_historical: true, published: "2022-01-02T23:30:00Z" }), "과거 기사 · 2022.01.03");
  assert.equal(historicalNewsLabel({ is_historical: true, published: "invalid" }), "과거 기사 · 게시일 확인 불가");
  assert.equal(historicalNewsLabel({ is_historical: false, published: "2022-01-02T23:30:00Z" }), "");
});

function harness(load, keys = ["BTC"], extra = {}) {
  let clock = 1000;
  let nextId = 0;
  const timers = new Map();
  const states = new Map();
  const queue = createNewsBriefingQueue({
    ...extra,
    keys, load, onChange: (key, state) => states.set(key, state), now: () => clock,
    setTimer: (fn, delay) => { timers.set(++nextId, { fn, dueAt: clock + delay }); return nextId; },
    clearTimer: (id) => timers.delete(id),
  });
  return {
    queue, states,
    delays: () => [...timers.values()].map((timer) => timer.dueAt - clock).sort((a, b) => a - b),
    async next() {
      const [id, timer] = [...timers].sort((a, b) => a[1].dueAt - b[1].dueAt)[0] || [];
      assert.ok(timer, "expected a scheduled request");
      timers.delete(id);
      clock = timer.dueAt;
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

test("ready headlines keep polling pending body summaries, then stop on ready or unavailable", async () => {
  for (const finalStatus of ["ready", "unavailable"]) {
    let calls = 0;
    const h = harness(async () => {
      calls += 1;
      const waiting = calls === 1;
      return { ...ready, items: [{ title: "CHIP 커뮤니티 의견", content_type: "community",
        community_summary_status: waiting ? "pending" : finalStatus,
        community_summary: waiting ? "" : "작성자는 거래량 증가를 관찰했어요." }],
      community_summaries: { status: waiting ? "partial" : "ready", pending_count: waiting ? 1 : 0,
        retry_after_seconds: 45 } };
    });
    h.queue.start();
    await h.next();
    assert.equal(h.states.get("BTC").data.translation.status, "ready");
    assert.deepEqual(h.delays(), [45000]);
    await h.next();
    assert.equal(calls, 2);
    assert.deepEqual(h.delays(), []);
    h.queue.stop();
  }
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
  assert.deepEqual(h.delays(), [45000, 45000]); // Only the two request deadlines.
  releases.get("BTC")();
  await flush();
  assert.deepEqual(calls, ["BTC", "ETH", "SOL"]);
  releases.get("ETH")(); releases.get("SOL")();
  await flush();
  assert.equal(peak, 2);
  assert.deepEqual(h.delays(), []);
  h.queue.stop();
});

test("a pending translation retries at its deadline while another card remains loading", async () => {
  const calls = [];
  const signals = new Map();
  let active = 0;
  let peak = 0;
  const h = harness(async (key, signal) => {
    calls.push(key);
    active += 1;
    peak = Math.max(peak, active);
    if (key === "SLOW") {
      signals.set(key, signal);
      signal.addEventListener("abort", () => { active -= 1; }, { once: true });
      return new Promise(() => {});
    }
    active -= 1;
    return key === "BTC" && calls.filter((value) => value === key).length === 1 ? pending : ready;
  }, ["BTC", "SLOW", "CFG"]);
  h.queue.start();
  await h.next();
  assert.deepEqual(calls, ["BTC", "SLOW", "CFG"]);
  assert.deepEqual(h.delays(), [30000, 45000]);
  await h.next();
  assert.deepEqual(calls, ["BTC", "SLOW", "CFG", "BTC"]);
  assert.equal(h.states.get("BTC").data.translation.status, "ready");
  assert.equal(h.states.get("SLOW").status, "loading");
  assert.equal(signals.get("SLOW").aborted, false);
  assert.equal(peak, 2);
  h.queue.stop();
  assert.equal(signals.get("SLOW").aborted, true);
  assert.deepEqual(h.delays(), []);
});

test("two stalled requests time out and release slots for the remaining cards", async () => {
  const calls = [];
  const signals = new Map();
  const h = harness((key, signal) => {
    calls.push(key);
    signals.set(key, signal);
    return key.startsWith("SLOW") ? new Promise(() => {}) : Promise.resolve(ready);
  }, ["SLOW1", "SLOW2", "BTC", "CFG"]);
  h.queue.start();
  await h.next();
  assert.deepEqual(calls, ["SLOW1", "SLOW2"]);
  await h.next();
  assert.equal(signals.get("SLOW1").aborted, true);
  assert.equal(h.states.get("SLOW1").status, "error");
  assert.equal(h.states.get("BTC").data.translation.status, "ready");
  assert.equal(h.states.get("CFG").data.translation.status, "ready");
  await h.next();
  assert.equal(signals.get("SLOW2").aborted, true);
  assert.deepEqual(calls, ["SLOW1", "SLOW2", "BTC", "CFG"]);
  assert.deepEqual(h.delays(), [30000]);
  h.queue.stop();
});

test("never-loaded cards precede retries when earlier slow cards repeatedly become due", async () => {
  const calls = [];
  const h = harness(async (key) => {
    calls.push(key);
    return key.startsWith("EARLY") ? new Promise(() => {}) : ready;
  }, ["EARLY1", "EARLY2", "EARLY3", "EARLY4", "BTC", "CFG"]);
  h.queue.start();
  await h.next(); // EARLY1 and EARLY2 start.
  await h.next(); // EARLY1 times out; EARLY3 starts.
  await h.next(); // EARLY2 times out; EARLY4 starts.
  await h.next(); // At 90s, earlier retries are overdue but BTC/CFG still have dueAt=0.
  assert.deepEqual(calls.slice(0, 6), ["EARLY1", "EARLY2", "EARLY3", "EARLY4", "BTC", "CFG"]);
  assert.equal(h.states.get("BTC").data.translation.status, "ready");
  assert.equal(h.states.get("CFG").data.translation.status, "ready");
  h.queue.stop();
});

test("a timed-out retry keeps ready headlines and discards its late response", async () => {
  let calls = 0;
  let release;
  const h = harness(async () => {
    calls += 1;
    if (calls === 1) return { ...pending, items: ready.items };
    return new Promise((resolve) => { release = resolve; });
  });
  h.queue.start();
  await h.next();
  await h.next();
  await h.next();
  const afterTimeout = h.states.get("BTC");
  assert.equal(afterTimeout.status, "error");
  assert.deepEqual(afterTimeout.data.items, ready.items);
  assert.deepEqual(h.delays(), [30000]);
  release({ ...ready, items: [{ title: "늦게 도착한 응답" }] });
  await flush();
  assert.equal(h.states.get("BTC"), afterTimeout);
  h.queue.stop();
});

test("manual retry uses a free slot while another card is still loading", async () => {
  const calls = [];
  const h = harness(async (key) => {
    calls.push(key);
    return key === "SLOW" ? new Promise(() => {}) : ready;
  }, ["BTC", "SLOW"]);
  h.queue.start();
  await h.next();
  h.queue.retry("BTC");
  await h.next();
  assert.deepEqual(calls, ["BTC", "SLOW", "BTC"]);
  assert.equal(h.states.get("SLOW").status, "loading");
  h.queue.stop();
});

test("an old translated snapshot keeps refreshing until fresh news arrives", async () => {
  let calls = 0;
  const h = harness(async () => ({ ...ready, stale: ++calls === 1 }));
  h.queue.start();
  await h.next();
  assert.equal(h.states.get("BTC").data.items.length, 1);
  assert.deepEqual(h.delays(), [30000]);
  await h.next();
  assert.equal(h.states.get("BTC").data.stale, false);
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

test("a late response from a hidden-page request cannot replace the resumed request", async () => {
  const releases = [];
  const signals = [];
  const h = harness((_key, signal) => {
    signals.push(signal);
    return new Promise((resolve) => releases.push(resolve));
  });
  h.queue.start();
  await h.next();
  h.queue.setVisible(false);
  assert.equal(signals[0].aborted, true);
  assert.deepEqual(h.delays(), []);
  h.queue.setVisible(true);
  await h.next();
  assert.equal(signals.length, 2);
  releases[0]({ ...ready, items: [{ title: "취소된 요청의 오래된 제목" }] });
  await flush();
  assert.equal(h.states.get("BTC").status, "loading");
  assert.equal(h.states.get("BTC").data, null);
  releases[1](ready);
  await flush();
  assert.deepEqual(h.states.get("BTC").data.items, ready.items);
  assert.deepEqual(h.delays(), []);
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

test("createNewsCache restores from storage, drops entries past max age, and survives a broken store", () => {
  const { createNewsCache } = libs;
  let clock = 100_000;
  const store = new Map();
  const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v) };
  const first = createNewsCache({ storage, now: () => clock, maxAgeMs: 1_000 });
  first.set("BTC", ready);
  const restored = createNewsCache({ storage, now: () => clock, maxAgeMs: 1_000 });
  assert.deepEqual(restored.get("BTC").data, ready);
  assert.equal(restored.isFresh("BTC", 500), true);
  clock += 2_000;
  assert.equal(restored.get("BTC"), null); // 너무 오래된 자료는 버린다
  const broken = createNewsCache({ storage: { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } } });
  broken.set("BTC", ready);
  assert.deepEqual(broken.get("BTC").data, ready);
});

test("a fresh cached briefing is shown at once and no request is sent", async () => {
  const { createNewsCache } = libs;
  let calls = 0;
  const cache = createNewsCache({ now: () => 1000 });
  cache.set("BTC", prepareNewsResponse(ready), 1000);
  const h = harness(async () => { calls += 1; return ready; }, ["BTC"], { cache, freshMs: 60_000 });
  h.queue.start();
  await h.next(); // start() 의 첫 pump — 신선한 캐시는 요청을 내지 않고 다음 예약도 없다
  assert.equal(calls, 0);
  assert.deepEqual(h.delays(), []);
});

test("a stale cached briefing stays visible while it refreshes, then the cache is replaced", async () => {
  const { createNewsCache } = libs;
  const cache = createNewsCache({ now: () => 1000 });
  const old = prepareNewsResponse({ ...ready, items: [{ id: "old", title: "어제 헤드라인" }] });
  cache.set("BTC", old, 1000 - 10 * 60_000);
  let release;
  const h = harness(() => new Promise((resolve) => { release = resolve; }), ["BTC"], { cache, freshMs: 5 * 60_000 });
  h.queue.start();
  await h.next();
  assert.equal(h.states.get("BTC").status, "success"); // 옛 자료를 보이는 채로 받는다
  assert.equal(h.states.get("BTC").data.items[0].id, "old");
  release(ready);
  await flush();
  assert.equal(h.states.get("BTC").data.items[0].id, "a");
  assert.equal(cache.get("BTC").data.items[0].id, "a");
  h.queue.stop();
});
