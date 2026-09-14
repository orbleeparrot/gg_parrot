import assert from "node:assert/strict";
import test from "node:test";
const storage = new Map();
globalThis.localStorage = { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) };
const { api } = await import("../src/api.js");
const { setAuth, updateAuthUser } = await import("../src/lib/auth.js");
const { leaderboardCacheVersion } = await import("../src/lib/cacheEvents.js");

test("public GETs omit credentials while private API reads and writes remain no-store", async () => {
  const requests = [];
  globalThis.fetch = async (path, options) => { requests.push({ path, ...options }); return new Response("{}"); };
  setAuth("fake-token", { id: 1, username: "fixture" });
  await api.newsMarket(); await api.symbols(); await api.hotCoins(10);
  await api.me(); await api.leaderboard("visitor"); await api.chatList(); await api.runnerKey();
  for (const request of requests.slice(0, 3)) {
    assert.equal(request.headers.Authorization, undefined);
    assert.equal(request.credentials, "omit");
  }
  for (const request of requests.slice(3)) {
    assert.equal(request.headers.Authorization, "Bearer fake-token");
    assert.equal(request.cache, "no-store");
  }
});

test("profile edits invalidate cached author names and leaderboard mutations invalidate slash choices", async () => {
  let gets = 0, author = "old";
  globalThis.fetch = async (path, options) => {
    if (path === "/api/me/profile") { author = "new"; return new Response(JSON.stringify({ user: { id: 1, username: author } })); }
    if (path.startsWith("/api/board/posts?")) { gets += 1; return new Response(JSON.stringify({ items: [{ id: 1, author_name: author }] })); }
    return new Response("{}");
  };
  await api.boardList();
  updateAuthUser((await api.updateProfile({ username: "new", bio: "" })).user);
  assert.equal((await api.boardList()).items[0].author_name, "new");
  assert.equal(gets, 2);
  const before = leaderboardCacheVersion();
  await api.leaderboardRegister({}, "fixture", "", "visitor");
  await api.leaderboardEdit(1, {}, "");
  await api.leaderboardDelete(1);
  await api.leaderboardUnlock(1);
  assert.equal(leaderboardCacheVersion(), before + 4);
});
