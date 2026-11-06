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

// 앱(PWA) 알림 — 서버(push_alerts)가 보낸 '내 에이전트 코인 소식'을 알림으로 띄운다.
// 받은 푸시마다 반드시 알림을 띄운다: iOS 는 알림 없이 끝난 푸시가 쌓이면 구독을 끊는다.
const NOTIFICATION_ICON = "/favicon/android-icon-192x192.png";
const NOTIFICATION_BADGE = "/favicon/notification-badge-96.png"; // 안드로이드 상태 표시줄(단색)

function appPath(value) {
  try {
    const url = new URL(String(value || "/"), self.location.origin);
    return url.origin === self.location.origin ? url.pathname + url.search + url.hash : "/";
  } catch {
    return "/";
  }
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = { body: event.data ? event.data.text() : "" };
  }
  const options = {
    body: String(data.body || "").slice(0, 400),
    icon: NOTIFICATION_ICON,
    badge: NOTIFICATION_BADGE,
    lang: "ko",
    data: { url: appPath(data.url) },
  };
  if (data.tag) options.tag = String(data.tag).slice(0, 120);
  event.waitUntil(self.registration.showNotification(String(data.title || "껄무새").slice(0, 120), options));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = appPath(event.notification.data && event.notification.data.url);
  event.waitUntil((async () => {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const open = windows.find((client) => new URL(client.url).origin === self.location.origin);
    if (open) {
      // 열린 앱이 있으면 새로 띄우지 않고 그 화면에서 옮긴다(lib/push.js 가 받아 라우터로 이동).
      await open.focus();
      open.postMessage({ type: "ggp:open", url: path });
      return;
    }
    await self.clients.openWindow(path);
  })());
});

// 알림을 켤 때만 새 워커를 바로 올린다 — 옛 워커(푸시 처리 없음)가 남아 첫 알림을 놓치지 않게(lib/push.js).
self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "ggp:skip-waiting") self.skipWaiting();
});
