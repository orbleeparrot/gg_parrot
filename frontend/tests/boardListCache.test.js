import test from "node:test";
import assert from "node:assert/strict";
import { createBoardListCache } from "../src/lib/boardListCache.js";

test("reuses fresh lists, separates account/query keys, and expires", async () => {
  let at = 0, calls = 0;
  const cache = createBoardListCache({ ttl: 30, now: () => at });
  const fetcher = async () => ({ items: [], call: ++calls });
  assert.equal((await cache.load("a:page1", fetcher)).call, 1);
  assert.equal((await cache.load("a:page1", fetcher)).call, 1);
  assert.equal((await cache.load("b:page1", fetcher)).call, 2);
  assert.equal((await cache.load("a:search", fetcher)).call, 3);
  at = 30;
  assert.equal((await cache.load("a:page1", fetcher)).call, 4);
});

test("mutation invalidation prevents a late list from refilling the cache", async () => {
  const cache = createBoardListCache();
  let finish, notices = 0;
  const unsubscribe = cache.subscribe(() => notices++);
  const old = cache.load("page", () => new Promise(resolve => { finish = resolve; }));
  cache.invalidate();
  await cache.load("page", async version => ({ items: [], version }));
  finish({ items: [], version: 0 }); await old;
  assert.equal(cache.peek("page").version, 1);
  assert.equal(notices, 1);
  unsubscribe(); cache.invalidate(); assert.equal(notices, 1);
});

test("failed requests are retried and cache capacity is bounded", async () => {
  const cache = createBoardListCache({ capacity: 2 });
  await assert.rejects(cache.load("a", async () => { throw Error("offline"); }));
  assert.equal(cache.peek("a"), undefined);
  for (const key of ["a", "b", "c"]) await cache.load(key, async () => ({ items: [] }));
  assert.equal(cache.peek("a"), undefined);
  assert.ok(cache.peek("c"));
});

test("detail updates cached counts without extending the freshness window", async () => {
  let at = 0;
  const cache = createBoardListCache({ ttl: 30, now: () => at });
  await cache.load("page", async () => ({ items: [{ id: 1, title: "title", views: 0 }] }));
  at = 20; cache.updatePost({ id: 1, views: 1, likes: 2, dislikes: 0 });
  assert.equal(cache.peek("page").items[0].views, 1);
  assert.equal(cache.peek("page").items[0].title, "title");
  at = 30; assert.equal(cache.peek("page"), undefined);
});
