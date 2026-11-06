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

// 열린 화면이 라우터로 옮기고 답하는지(hooks/usePushBridge.js). 오프라인 안내 화면(offline.html) · 받는 코드가 없는 옛 화면은
// 답이 없다. 기다림은 짧게 — 브라우저는 알림을 누른 뒤 잠깐만 창 열기를 허락한다.
const OPEN_ACK_MS = 800;

function routedInPlace(client, path) {
  return new Promise((resolve) => {
    let channel;
    try {
      channel = new MessageChannel();
    } catch {
      resolve(false);
      return;
    }
    const finish = (value) => {
      clearTimeout(timer);
      channel.port1.close();
      resolve(value);
    };
    const timer = setTimeout(() => finish(false), OPEN_ACK_MS);
    channel.port1.onmessage = () => finish(true);
    try {
      client.postMessage({ type: "ggp:open", url: path }, [channel.port2]);
    } catch {
      finish(false);
    }
  });
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const path = appPath(event.notification.data && event.notification.data.url);
  event.waitUntil((async () => {
    const windows = (await self.clients.matchAll({ type: "window", includeUncontrolled: true }))
      .filter((client) => new URL(client.url).origin === self.location.origin);
    const open = windows.find((client) => client.focused)
      || windows.find((client) => client.visibilityState === "visible") || windows[0];
    if (open) {
      // 열린 앱이 있으면 새로 띄우지 않고 그 화면에서 옮긴다. 답이 없으면 그 창을 그 주소로 다시 연다.
      try {
        await open.focus();
      } catch {
        /* 이미 앞에 있다 */
      }
      if (await routedInPlace(open, path)) return;
      try {
        if (await open.navigate(path)) return;
      } catch {
        /* 이 워커가 맡지 않은 창 — 새 창으로 */
      }
    }
    await self.clients.openWindow(path);
  })());
});

// 앱 화면과 워커 사이(lib/push.js) — ① ping: 이 워커가 푸시를 처리한다고 답한다(옛 워커는 답이 없다 → 화면이 새 워커를 찾는다).
// ② skip-waiting: 알림을 켜는데 지금 워커가 옛 워커일 때만 온다. 열린 창이 요청한 그 하나일 때만 바로 올린다 — 다른 창이
// 옛 파일로 돌고 있으면 activate 가 옛 캐시를 지워 그 창의 다음 화면 파일이 404 가 되므로, 그 창들이 닫힐 때까지 기다린다.
self.addEventListener("message", (event) => {
  const type = event.data && event.data.type;
  const reply = event.ports && event.ports[0];
  if (type === "ggp:ping") {
    if (reply) reply.postMessage({ push: true });
    return;
  }
  if (type === "ggp:skip-waiting") {
    event.waitUntil((async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const alone = windows.length <= 1;
      if (reply) reply.postMessage({ skipping: alone });
      if (alone) await self.skipWaiting();
    })());
  }
});
