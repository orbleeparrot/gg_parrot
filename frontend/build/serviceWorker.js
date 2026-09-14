/* Generated build allowlist. No account/API/news response is stored here. */
const CACHE_PREFIX = "ggparrot-static-";
const CACHE_NAME = CACHE_PREFIX + "__CACHE_VERSION__";
const BUILD_ASSETS = new Set(__BUILD_ASSETS__);

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const response = await fetch("/offline.html", { cache: "reload", credentials: "omit" });
    if (!response.ok || !response.headers.get("content-type")?.includes("text/html")) {
      throw new Error("Offline page unavailable");
    }
    await (await caches.open(CACHE_NAME)).put("/offline.html", response);
  })());
  // New releases wait for existing tabs to close; never replace their JS midway.
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names.filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== self.location.origin
    || url.pathname === "/api" || url.pathname.startsWith("/api/")
    || request.headers.has("authorization")) return;
  if (request.mode === "navigate") {
    // Only a generic offline page is retained. Real navigation responses can
    // contain login/reset parameters and must never enter Cache Storage.
    event.respondWith(fetch(request).catch(async () =>
      (await caches.open(CACHE_NAME)).match("/offline.html")));
    return;
  }
  if (url.search || !BUILD_ASSETS.has(url.pathname)) return;
  event.respondWith((async () => {
    const cache = await caches.open(CACHE_NAME);
    const cached = await cache.match(url.pathname);
    if (cached) return cached;
    const response = await fetch(request);
    const contentType = response.headers.get("content-type") || "";
    // Missing asset SPA fallbacks must not be saved as immutable JavaScript.
    if (response.ok && !response.redirected && !contentType.includes("text/html")
      && !/no-store|private/i.test(response.headers.get("cache-control") || "")) {
      const copy = response.clone();
      event.waitUntil(cache.put(url.pathname, copy).catch(() => {}));
    }
    return response;
  })());
});
