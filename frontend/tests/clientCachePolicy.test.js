import assert from "node:assert/strict";
import test from "node:test";
import { createNewsCache } from "../src/lib/newsBriefings.js";
import { createSharedResource } from "../src/lib/sharedResource.js";
import { CHAT_CACHE_LIMIT } from "../src/lib/chatFeed.js";
import { chatCacheScopeCount, getChatFeed, observeChat, receiveChat } from "../src/lib/chatStore.js";

const flush = () => new Promise(setImmediate);
function harness(load, options = {}) {
  let clock = 1_000, sequence = 0;
  const timers = new Map(), events = new Map();
  const doc = { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: (name) => events.delete(name) };
  const resource = createSharedResource(load, { ttlMs: 45_000, retryMs: 30_000, ...options,
    now: () => clock, documentRef: doc,
    setTimer: (fn, delay) => { timers.set(++sequence, { fn, at: clock + delay }); return sequence; },
    clearTimer: (id) => timers.delete(id),
  });
  return { resource, timers,
    advance: (ms) => { clock += ms; },
    async next() {
      const [id, timer] = [...timers].sort((a, b) => a[1].at - b[1].at)[0];
      clock = timer.at; timers.delete(id); timer.fn(); await flush();
    },
    visible(value) { doc.hidden = !value; events.get("visibilitychange")?.(); },
  };
}

test("two public consumers share one polling loop, keep data on failure and pause while hidden", async () => {
  let calls = 0, fail = false;
  const h = harness(async () => { calls += 1; if (fail) throw Error("offline"); return { coins: [{ symbol: "BTC", price: calls }] }; });
  const stopA = h.resource.subscribe(() => {}), stopB = h.resource.subscribe(() => {});
  assert.equal(h.timers.size, 1);
  await h.next(); assert.equal(calls, 1);
  await h.next(); assert.equal(calls, 2);
  const prior = h.resource.getSnapshot().data;
  fail = true; await h.next();
  assert.equal(h.resource.getSnapshot().data, prior);
  assert.equal(h.resource.getSnapshot().error, "offline");
  h.visible(false); assert.equal(h.timers.size, 0);
  h.advance(60_000); fail = false; h.visible(true); await h.next();
  assert.equal(calls, 4);
  stopA(); assert.equal(h.timers.size, 1);
  stopB(); assert.equal(h.timers.size, 0);
});

test("symbol data expires and stale or empty results retry before the normal five minute TTL", async () => {
  let calls = 0;
  const h = harness(async () => ({ items: ++calls <= 2 ? [] : ["NEWUSDT"], stale: calls === 1 }),
    { ttlMs: 300_000, isStale: (data) => data.stale || !data.items.length });
  const stop = h.resource.subscribe(() => {});
  await h.next(); assert.equal(calls, 1);
  await h.resource.refresh(); assert.equal(calls, 1);
  await h.next(); assert.equal(calls, 2);
  await h.next(); assert.equal(calls, 3);
  h.advance(299_999); await h.resource.refresh(); assert.equal(calls, 3);
  h.advance(1); await h.resource.refresh(); assert.equal(calls, 4);
  stop();
});

test("a hidden-page late response cannot replace the resumed shared resource", async () => {
  const releases = [], signals = [];
  const h = harness((signal) => new Promise((resolve) => { signals.push(signal); releases.push(resolve); }));
  const stop = h.resource.subscribe(() => {});
  await h.next(); h.visible(false);
  assert.equal(signals[0].aborted, true);
  h.visible(true); await h.next();
  releases[1]({ value: "current" }); await flush();
  releases[0]({ value: "obsolete" }); await flush();
  assert.equal(h.resource.getSnapshot().data.value, "current");
  stop();
});

test("news persistence bounds cardinality and UTF-8 bytes, evicts expired entries globally and restores safely", () => {
  let clock = 1000, stored = null;
  const storage = { getItem: () => stored, setItem: (_key, value) => { stored = value; } };
  const options = { storage, now: () => clock, maxEntries: 3, maxBytes: 500, maxAgeMs: 100 };
  const cache = createNewsCache(options);
  for (let i = 0; i < 10; i += 1) cache.set(String(i), { title: "한글 기사" });
  assert.equal(cache.size(), 3); assert.equal(cache.get("0"), null);
  assert.ok(new TextEncoder().encode(stored).length <= 500);
  cache.set("oversized", { title: "긴 뉴스".repeat(1000) });
  assert.equal(cache.get("oversized"), null);
  clock += 101; cache.set("new", { title: "새 기사" });
  assert.deepEqual(Object.keys(JSON.parse(stored)), ["new"]);
  const restored = createNewsCache(options);
  assert.equal(restored.get("new").data.title, "새 기사");
  clock += 101;
  assert.equal(restored.get("new"), null);
  assert.deepEqual(JSON.parse(stored), {});
});

const DAY = Date.UTC(2026, 8, 14);
const rows = (first, last) => Array.from({ length: last - first + 1 }, (_, i) => ({ id: first + i, user_id: 2, text: "message", created_ms: DAY + 1000 }));
let revision = 0;
const page = (first, last, extra = {}) => ({ items: rows(first, last), latest_id: 1200, seen_id: 0,
  unseen_count: 1200, day_start_ms: DAY, has_more: first > 1, snapshot_ms: ++revision, ...extra });

test("bounded chat history traverses evicted newer pages without losing older-page availability", () => {
  const scope = "cache-history";
  receiveChat(scope, page(1001, 1200));
  for (let first = 801; first >= 1; first -= 200) receiveChat(scope, page(first, first + 199), { older: true, beforeId: first + 200 });
  let feed = getChatFeed(scope);
  assert.equal(feed.items.length, CHAT_CACHE_LIMIT);
  assert.equal(feed.items[0].id, 1);
  assert.equal(feed.items.at(-1).id, 1000);
  assert.equal(feed.tailEvicted, true);
  receiveChat(scope, page(1001, 1200, { mode: "delta", fetched_through_id: 1200, has_more_new: false }), { forward: true });
  feed = getChatFeed(scope);
  assert.equal(feed.items.length, CHAT_CACHE_LIMIT);
  assert.equal(feed.items[0].id, 201);
  assert.equal(feed.items.at(-1).id, 1200);
  assert.equal(feed.tailEvicted, false);
  assert.equal(feed.hasMore, true);
});

test("new arrivals do not evict an older reading window and latest-page reload restores a contiguous page", () => {
  const scope = "cache-pinned";
  receiveChat(scope, page(1, 1000, { latest_id: 1000 }));
  receiveChat(scope, page(1001, 1200, { mode: "delta", fetched_through_id: 1200, has_more_new: false }), { keepOlder: true });
  assert.equal(getChatFeed(scope).items[0].id, 1);
  assert.equal(getChatFeed(scope).tailEvicted, true);
  receiveChat(scope, page(1201, 1400, { latest_id: 1400 }));
  const feed = getChatFeed(scope);
  assert.equal(feed.items.length, 200);
  assert.equal(feed.items[0].id, 1201);
  assert.equal(feed.tailEvicted, false);
  assert.equal(feed.pageLatestId, 1400);
});

test("inactive account chat caches are evicted while an observed account remains intact", () => {
  const stop = observeChat("active-cache", () => {});
  const active = getChatFeed("active-cache");
  for (let i = 0; i < 20; i += 1) getChatFeed(`inactive-${i}`);
  assert.equal(getChatFeed("active-cache"), active);
  assert.ok(chatCacheScopeCount() <= 5);
  stop();
  assert.ok(chatCacheScopeCount() <= 3);
});
