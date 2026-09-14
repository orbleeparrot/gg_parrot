import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

function harness() {
  const hooks = new Map();
  const stores = new Map();
  const calls = [];
  let network = async (url) => new Response(url === "/offline.html" ? "offline fixture" : "export const fixture = 1", {
    headers: { "content-type": url === "/offline.html" ? "text/html" : "application/javascript" },
  });
  const cache = async (name) => {
    if (!stores.has(name)) stores.set(name, new Map());
    const values = stores.get(name);
    return { put: async (key, response) => values.set(key, response.clone()),
      match: async (key) => values.get(key)?.clone() };
  };
  const source = readFileSync(new URL("../build/serviceWorker.js", import.meta.url), "utf8")
    .replace("__CACHE_VERSION__", "fixture")
    .replace("__BUILD_ASSETS__", JSON.stringify(["/assets/app-12345678.js", "/assets/page-12345678.js"]));
  vm.runInNewContext(source, {
    URL, Set, Promise, Error,
    self: { location: { origin: "https://ggparrot.test" }, clients: { claim: async () => {} },
      addEventListener: (name, fn) => hooks.set(name, fn) },
    caches: { open: cache, keys: async () => [...stores.keys()], delete: async (name) => stores.delete(name) },
    fetch: async (request) => { const url = typeof request === "string" ? request : request.url; calls.push(url); return network(url); },
  });
  return { stores, calls, setNetwork: (fn) => { network = fn; },
    async event(name, request) {
      const pending = [];
      let response;
      hooks.get(name)({ request, waitUntil: (promise) => pending.push(promise), respondWith: (promise) => { response = promise; } });
      const value = await response;
      await Promise.all(pending);
      return value;
    } };
}

function request(path, { method = "GET", mode = "cors", authorization = false } = {}) {
  return { url: new URL(path, "https://ggparrot.test").href, method, mode,
    headers: new Headers(authorization ? { authorization: "Bearer fixture" } : {}) };
}

test("worker caches only build assets and removes only its obsolete cache", async () => {
  const h = harness();
  h.stores.set("another-app-cache", new Map());
  h.stores.set("ggparrot-static-old", new Map());
  await h.event("install");
  await h.event("activate");
  assert(h.stores.has("another-app-cache"));
  assert(!h.stores.has("ggparrot-static-old"));
  await h.event("fetch", request("/assets/app-12345678.js"));
  await h.event("fetch", request("/assets/app-12345678.js"));
  assert.equal(h.calls.filter((url) => url.endsWith("app-12345678.js")).length, 1);
  assert.equal(await h.event("fetch", request("/assets/unknown-12345678.js")), undefined);
});

test("API, authenticated, cross-origin and write requests bypass the worker", async () => {
  const h = harness();
  for (const req of [request("/api/me/runner/key"), request("/api/news/market"),
    request("/api/auth/me", { mode: "navigate" }), request("/assets/app-12345678.js", { method: "POST" }),
    request("/assets/app-12345678.js", { authorization: true }), request("https://external.test/app.js")]) {
    assert.equal(await h.event("fetch", req), undefined);
  }
  assert.equal(h.calls.length, 0);
  assert.equal(h.stores.size, 0);
});

test("navigation stays network-first and private HTML is never stored", async () => {
  const h = harness();
  await h.event("install");
  h.setNetwork(async () => new Response("personal HTML", { headers: { "content-type": "text/html" } }));
  assert.equal(await (await h.event("fetch", request("/reset?token=fixture", { mode: "navigate" }))).text(), "personal HTML");
  assert.deepEqual([...h.stores.get("ggparrot-static-fixture").keys()], ["/offline.html"]);
  h.setNetwork(async () => { throw new Error("offline"); });
  assert.equal(await (await h.event("fetch", request("/news", { mode: "navigate" }))).text(), "offline fixture");
});

test("missing asset HTML, failures and no-store responses are not cached", async () => {
  const h = harness();
  for (const response of [new Response("SPA fallback", { headers: { "content-type": "text/html" } }),
    new Response("down", { status: 503 }), new Response("private", { headers: { "cache-control": "no-store" } })]) {
    h.setNetwork(async () => response.clone());
    await h.event("fetch", request("/assets/page-12345678.js"));
    assert.equal(h.stores.get("ggparrot-static-fixture").size, 0);
  }
});

test("Vercel SPA rewrite excludes APIs and static resources", () => {
  const config = JSON.parse(readFileSync(new URL("../vercel.json", import.meta.url), "utf8"));
  const pattern = new RegExp(`^${config.rewrites.at(-1).source}$`);
  for (const path of ["/", "/news", "/agents", "/board/123"]) assert(pattern.test(path));
  for (const path of ["/api", "/api/news/market", "/assets/missing.js", "/sw.js", "/offline.html", "/brand/missing.svg"]) assert(!pattern.test(path));
});
