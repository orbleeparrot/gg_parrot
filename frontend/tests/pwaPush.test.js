import test from "node:test";
import assert from "node:assert/strict";
import { api } from "../src/api.js";
import { detectBrowser, detectPlatform, promptInstall, installState, resetInstallStateForTest } from "../src/lib/pwa.js";
import { disablePush, enablePush, openTarget, pushSupport, readPushState, syncPush, urlBase64ToUint8Array } from "../src/lib/push.js";

const UA = {
  iphone: "Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Mobile/15E148 Safari/604.1",
  iphoneChrome: "Mozilla/5.0 (iPhone; CPU iPhone OS 26_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/142.0 Mobile/15E148 Safari/604.1",
  ipad: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.1 Safari/605.1.15",
  android: "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36",
  samsung: "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/28.0 Chrome/130.0.0.0 Mobile Safari/537.36",
  kakao: "Mozilla/5.0 (Linux; Android 15; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36 KAKAOTALK 25.9.0",
  windowsEdge: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
  mac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
};

test("기기 · 브라우저 구분 — 아이패드는 터치 되는 맥, 카카오톡은 앱 안 브라우저", () => {
  assert.equal(detectPlatform({ userAgent: UA.iphone }), "ios");
  assert.equal(detectPlatform({ userAgent: UA.ipad, platform: "MacIntel", maxTouchPoints: 5 }), "ios");
  assert.equal(detectPlatform({ userAgent: UA.mac, platform: "MacIntel", maxTouchPoints: 0 }), "desktop");
  assert.equal(detectPlatform({ userAgent: UA.android }), "android");
  assert.equal(detectPlatform({ userAgent: UA.windowsEdge }), "desktop");
  assert.equal(detectBrowser({ userAgent: UA.iphone }), "safari");
  assert.equal(detectBrowser({ userAgent: UA.iphoneChrome }), "ios-other");
  assert.equal(detectBrowser({ userAgent: UA.android }), "chrome");
  assert.equal(detectBrowser({ userAgent: UA.samsung }), "samsung");
  assert.equal(detectBrowser({ userAgent: UA.kakao }), "in-app");
  assert.equal(detectBrowser({ userAgent: UA.windowsEdge }), "edge");
});

test("바로 설치 — 받아 둔 이벤트로 한 번만 띄운다", async () => {
  let prompted = 0;
  resetInstallStateForTest({ prompt: async () => { prompted += 1; }, userChoice: Promise.resolve({ outcome: "accepted" }) });
  assert.equal(installState().canPrompt, true);
  assert.equal(await promptInstall(), "accepted");
  assert.equal(prompted, 1);
  assert.equal(installState().canPrompt, false);
  assert.equal(await promptInstall(), "unavailable");
});

test("공개키 base64url → 바이트", () => {
  assert.deepEqual([...urlBase64ToUint8Array("AQID_-8")], [1, 2, 3, 255, 239]);
});

test("알림을 눌렀을 때 같은 사이트 경로로만 옮긴다", () => {
  const origin = "https://gg-parrot.vercel.app";
  assert.equal(openTarget({ type: "ggp:open", url: "/agents?session=41" }, origin), "/agents?session=41");
  assert.equal(openTarget({ type: "ggp:open", url: "https://evil.example/x" }, origin), "");
  assert.equal(openTarget({ type: "other", url: "/agents" }, origin), "");
});

// --- 브라우저 흉내 ------------------------------------------------------------------
function fakeStorage() {
  const map = new Map();
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)), removeItem: (k) => map.delete(k) };
}

function fakeBrowser({ userAgent = UA.android, permission = "default", answer = "granted", standalone = false } = {}) {
  const state = { subscription: null, unsubscribed: 0, permissionAsked: 0 };
  const makeSubscription = (key) => ({
    endpoint: "https://fcm.googleapis.com/fcm/send/abc",
    options: { applicationServerKey: key.buffer },
    toJSON: () => ({ endpoint: "https://fcm.googleapis.com/fcm/send/abc", keys: { p256dh: "P", auth: "A" } }),
    unsubscribe: async () => { state.unsubscribed += 1; state.subscription = null; return true; },
  });
  const pushManager = {
    getSubscription: async () => state.subscription,
    subscribe: async ({ applicationServerKey }) => { state.subscription = makeSubscription(applicationServerKey); return state.subscription; },
  };
  const registrationObject = { pushManager, update: async () => {}, installing: null, waiting: null };
  const Notification = {
    permission,
    requestPermission: () => { state.permissionAsked += 1; Notification.permission = answer; return Promise.resolve(answer); },
  };
  const win = {
    navigator: { userAgent, platform: "Linux", maxTouchPoints: 5, standalone,
      serviceWorker: { ready: Promise.resolve(registrationObject), getRegistration: async () => registrationObject } },
    PushManager: function PushManager() {},
    Notification,
    matchMedia: () => ({ matches: standalone }),
  };
  return { win, state };
}

const CONFIG = { enabled: true, public_key: "AQID" };

test("아이폰 사파리 탭에서는 '홈 화면에 추가해야 된다', 카카오톡 안에서는 앱 안 브라우저", () => {
  const iphone = { navigator: { userAgent: UA.iphone, platform: "iPhone", maxTouchPoints: 5 }, matchMedia: () => ({ matches: false }) };
  assert.equal(pushSupport(iphone), "needs-install");
  const kakao = fakeBrowser({ userAgent: UA.kakao }).win;
  assert.equal(pushSupport(kakao), "in-app");
});

test("켜기 → 서버 등록 · 켠 계정 기억, 끄기 → 서버 해제 · 브라우저 구독 끊기", async (t) => {
  globalThis.localStorage = fakeStorage();
  const calls = [];
  t.mock.method(api, "pushSubscribe", async (body) => { calls.push(["put", body]); return { subscribed: true }; });
  t.mock.method(api, "pushUnsubscribe", async (endpoint) => { calls.push(["delete", endpoint]); return { subscribed: false }; });
  const { win, state } = fakeBrowser();
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "off");
  assert.equal(await readPushState({ userId: 7, config: { enabled: false }, win }), "unconfigured");
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(state.permissionAsked, 1);
  assert.deepEqual(calls[0], ["put", { endpoint: "https://fcm.googleapis.com/fcm/send/abc", keys: { p256dh: "P", auth: "A" }, platform: "android" }]);
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(await readPushState({ userId: 8, config: CONFIG, win }), "off", "다른 계정에게는 꺼진 것");
  assert.equal(await disablePush({ win }), "off");
  assert.deepEqual(calls[1], ["delete", "https://fcm.googleapis.com/fcm/send/abc"]);
  assert.equal(state.unsubscribed, 1);
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "off");
});

test("권한을 거절하면 구독하지 않는다", async (t) => {
  globalThis.localStorage = fakeStorage();
  const put = t.mock.method(api, "pushSubscribe", async () => ({}));
  const { win, state } = fakeBrowser({ answer: "denied" });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "denied");
  assert.equal(state.subscription, null);
  assert.equal(put.mock.callCount(), 0);
  win.Notification.permission = "denied";
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "denied");
});

test("다른 계정이 켜 둔 구독은 로그인한 사람에게 옮기지 않고 끊는다", async (t) => {
  globalThis.localStorage = fakeStorage();
  const put = t.mock.method(api, "pushSubscribe", async () => ({}));
  const del = t.mock.method(api, "pushUnsubscribe", async () => ({}));
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  assert.equal(put.mock.callCount(), 1);
  assert.equal(await syncPush({ userId: 7, win }), "fresh", "방금 맞춘 기기는 다시 묻지 않는다");
  assert.equal(await syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 }), "synced");
  assert.equal(put.mock.callCount(), 2);
  assert.equal(await syncPush({ userId: 9, win }), "dropped");
  assert.equal(state.unsubscribed, 1);
  assert.equal(del.mock.callCount(), 0, "로그아웃 뒤 정리는 서버에 묻지 않는다(410 으로 지워진다)");
  assert.equal(put.mock.callCount(), 2, "9번 계정으로 옮기지 않는다");
});
