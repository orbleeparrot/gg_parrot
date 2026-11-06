import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { api } from "../src/api.js";
import { clearAuth, setAuth } from "../src/lib/auth.js";
import { detectBrowser, detectPlatform, offersAppInstall, promptInstall, installState, resetInstallStateForTest } from "../src/lib/pwa.js";
import {
  disablePush, enablePush, forgetPushStatus, loadPushConfig, openTarget, pushSupport, readPushState, setPushTimingForTest,
  syncPush, urlBase64ToUint8Array,
} from "../src/lib/push.js";
import { enableToast, testToast } from "../src/hooks/usePushState.js";

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

// 서비스 워커 하나 — answers 에 적힌 말에만 답한다(옛 워커는 아무 말에도 답하지 않는다). ping 을 붙잡아 두려면 onPing.
function fakeWorker({ state = "activated", answers = {}, onPing = null } = {}) {
  const listeners = new Set();
  const worker = {
    state, asked: [], onActivate: null,
    addEventListener: (_, fn) => listeners.add(fn),
    removeEventListener: (_, fn) => listeners.delete(fn),
    postMessage(message, ports) {
      worker.asked.push(message.type);
      if (message.type === "ggp:ping" && onPing) {
        onPing(ports[0]);
        return;
      }
      const answer = answers[message.type];
      if (!answer) return;
      ports?.[0]?.postMessage(answer);
      if (answer.skipping) {
        worker.state = "activated";
        worker.onActivate?.();
        listeners.forEach((fn) => fn());
      }
    },
  };
  return worker;
}

let endpoints = 0;
function fakeBrowser({ userAgent = UA.android, permission = "default", answer = "granted", standalone = false,
  active = fakeWorker({ answers: { "ggp:ping": { push: true } } }), waiting = null, beforeSubscribe = null } = {}) {
  const state = { subscription: null, unsubscribed: 0, permissionAsked: 0, updates: 0 };
  const makeSubscription = (key) => {
    const endpoint = `https://fcm.googleapis.com/fcm/send/${(endpoints += 1)}`;
    const subscription = {
      endpoint,
      options: { applicationServerKey: key.buffer },
      toJSON: () => ({ endpoint, keys: { p256dh: "P", auth: "A" } }),
      unsubscribe: async () => {
        state.unsubscribed += 1;
        if (state.subscription === subscription) state.subscription = null;
        return true;
      },
    };
    return subscription;
  };
  const pushManager = {
    getSubscription: async () => state.subscription,
    subscribe: async ({ applicationServerKey }) => {
      if (beforeSubscribe) await beforeSubscribe();
      state.subscription = makeSubscription(applicationServerKey);
      return state.subscription;
    },
  };
  const registrationObject = { pushManager, update: async () => { state.updates += 1; }, installing: null, waiting, active };
  if (waiting) {
    waiting.onActivate = () => { // 새 워커가 올라오면 등록의 active 가 바뀐다
      registrationObject.active = waiting;
      registrationObject.waiting = null;
    };
  }
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
const PUSHES = { "ggp:ping": { push: true } };

function signIn(t, id) {
  globalThis.localStorage = fakeStorage();
  clearAuth();
  setAuth(`token-${id}`, { id });
  forgetPushStatus();
  setPushTimingForTest({ ping: 40, finalPing: 40 }); // 답하지 않는 옛 워커를 1.5초씩 기다리지 않게
  t.after(() => {
    clearAuth();
    setPushTimingForTest();
  });
}

test("아이폰 사파리 탭에서는 '홈 화면에 추가해야 된다', 카카오톡 안에서는 앱 안 브라우저", () => {
  const iphone = { navigator: { userAgent: UA.iphone, platform: "iPhone", maxTouchPoints: 5 }, matchMedia: () => ({ matches: false }) };
  assert.equal(pushSupport(iphone), "needs-install");
  const kakao = fakeBrowser({ userAgent: UA.kakao }).win;
  assert.equal(pushSupport(kakao), "in-app");
});

test("켜기 → 서버 등록 · 켠 계정 기억, 끄기 → 서버 해제 · 브라우저 구독 끊기", async (t) => {
  signIn(t, 7);
  const calls = [];
  t.mock.method(api, "pushSubscribe", async (body) => { calls.push(["put", body]); return { subscribed: true }; });
  t.mock.method(api, "pushUnsubscribe", async (endpoint) => { calls.push(["delete", endpoint]); return { subscribed: false }; });
  t.mock.method(api, "pushStatus", async () => ({ subscribed: true }));
  const { win, state } = fakeBrowser();
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "off");
  assert.equal(await readPushState({ userId: 7, config: { enabled: false }, win }), "unconfigured");
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(state.permissionAsked, 1);
  const endpoint = state.subscription.endpoint;
  assert.deepEqual(calls[0], ["put", { endpoint, keys: { p256dh: "P", auth: "A" }, platform: "android" }]);
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(await readPushState({ userId: 8, config: CONFIG, win }), "off", "다른 계정에게는 꺼진 것");
  assert.equal(await disablePush({ win }), "off");
  assert.deepEqual(calls[1], ["delete", endpoint]);
  assert.equal(state.unsubscribed, 1);
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "off");
});

test("권한을 거절하면 구독하지 않는다", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({}));
  const { win, state } = fakeBrowser({ answer: "denied" });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "denied");
  assert.equal(state.subscription, null);
  assert.equal(put.mock.callCount(), 0);
  win.Notification.permission = "denied";
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "denied");
});

test("다른 계정이 켜 둔 구독은 로그인한 사람에게 옮기지 않고 끊는다", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({}));
  const del = t.mock.method(api, "pushUnsubscribe", async () => ({}));
  t.mock.method(api, "pushStatus", async () => ({ subscribed: true }));
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

// --- 리뷰 P1: 켜는 도중 계정이 바뀌면 ------------------------------------------------------
test("켜는 도중 다른 탭에서 B 로 로그인하면 — 그만두고, 그사이 만든 구독도 끊는다(B 로 등록하지 않는다)", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  const { win, state } = fakeBrowser({ beforeSubscribe: async () => {
    clearAuth();
    setAuth("token-8", { id: 8 });
  } });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "cancelled");
  assert.equal(put.mock.callCount(), 0, "B 의 토큰으로 서버에 등록하지 않는다");
  assert.equal(state.subscription, null, "그사이 만든 구독은 끊는다");
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), null, "A 를 켠 계정으로 적지 않는다");
  assert.equal(await readPushState({ userId: 8, config: CONFIG, win }), "off");
  assert.equal(enableToast("cancelled"), null, "토스트도 띄우지 않는다");
});

test("서버 등록 응답을 기다리는 동안 계정이 바뀌어도 — 기기에 A 를 적지 않고 구독을 끊는다", async (t) => {
  signIn(t, 7);
  t.mock.method(api, "pushSubscribe", async () => {
    clearAuth();
    setAuth("token-8", { id: 8 });
    return { subscribed: true };
  });
  const { win, state } = fakeBrowser();
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "cancelled");
  assert.equal(state.subscription, null);
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), null);
});

// --- 리뷰 P2: 서버가 기기를 지웠을 때 -------------------------------------------------------
test("서버가 이 기기를 지웠으면 '꺼짐' — 같이 읽어도 서버에는 한 번 묻고, 다시 켜면 새 구독으로 등록한다", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  let subscribed = true;
  const status = t.mock.method(api, "pushStatus", async () => ({ subscribed }));
  const { win } = fakeBrowser();
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(status.mock.callCount(), 0, "방금 켠 기기는 서버에 다시 묻지 않는다");
  forgetPushStatus();
  subscribed = false; // 11번째 기기로 밀림 · 끝난 구독(410) · 거절 반복
  const reads = await Promise.all([1, 2, 3].map(() => readPushState({ userId: 7, config: CONFIG, win })));
  assert.deepEqual(reads, ["off", "off", "off"]);
  assert.equal(status.mock.callCount(), 1, "설정 · 단추 · 첫 묻기가 같이 읽어도 서버에는 한 번");
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), "7", "읽기는 저장소를 건드리지 않는다");
  // 다시 켜면 남은 구독을 다시 쓰지 않고 새 구독으로 등록한다
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(put.mock.callCount(), 2);
  assert.notEqual(put.mock.calls[1].arguments[0].endpoint, put.mock.calls[0].arguments[0].endpoint);
});

test("옛 구독에 대한 늦은 '없음' 답이 그사이 켠 새 구독을 지우지 않는다", async (t) => {
  signIn(t, 7);
  t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  forgetPushStatus();
  let reply;
  let asked;
  const reached = new Promise((resolve) => { asked = resolve; });
  t.mock.method(api, "pushStatus", () => {
    asked();
    return new Promise((resolve) => { reply = resolve; });
  });
  const oldRead = readPushState({ userId: 7, config: CONFIG, win }); // 옛 구독 상태를 묻는 중
  await reached;
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on"); // 다른 곳에서 새 구독으로 켰다
  const fresh = state.subscription.endpoint;
  reply({ subscribed: false }); // 옛 구독에 대한 답이 늦게 온다
  assert.equal(await oldRead, "off", "그 답은 옛 구독 것 — 화면은 다음 읽기(순번)가 맞춘다");
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), "7");
  assert.equal(await syncPush({ userId: 7, win }), "fresh");
  assert.equal(state.subscription?.endpoint, fresh, "새 구독은 그대로");
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
});

test("A 의 켜기가 워커를 기다리는 사이 B 로 로그인해 켜면 — A 는 아무 구독도 건드리지 않고 그만둔다", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  let held;
  let pings = 0;
  const reached = new Promise((resolve) => { held = resolve; });
  const active = fakeWorker({ onPing: (port) => {
    pings += 1;
    if (pings === 1) held(port); // A 의 첫 ping 을 붙잡아 둔다
    else port.postMessage({ push: true });
  } });
  const { win, state } = fakeBrowser({ active });
  const enableA = enablePush({ userId: 7, config: CONFIG, win });
  const port = await reached;
  clearAuth();
  setAuth("token-8", { id: 8 });
  const enableB = enablePush({ userId: 8, config: CONFIG, win });
  port.postMessage({ push: true }); // A 가 다시 움직인다
  assert.equal(await enableA, "cancelled");
  assert.equal(await enableB, "on", "B 는 A 가 끝난 뒤 차례로 켠다");
  assert.ok(state.subscription, "B 의 구독이 남아 있다");
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), "8");
  assert.equal(put.mock.callCount(), 1, "서버 등록은 B 한 번");
  assert.equal(state.unsubscribed, 0, "A 가 끊은 구독이 없다");
});

test("12시간 재등록 때도 서버가 지운 기기는 되살리지 않는다 · 서버에 못 물으면 브라우저를 믿는다", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  let answer = async () => ({ subscribed: false });
  t.mock.method(api, "pushStatus", () => answer());
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  answer = async () => { throw new Error("offline"); };
  forgetPushStatus();
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on", "못 물으면 그대로");
  assert.equal(await syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 }), "skip");
  answer = async () => ({ subscribed: false });
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on", "못 물은 답(null)은 잠깐 같이 쓴다");
  assert.equal(await syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 }), "dropped");
  assert.equal(put.mock.callCount(), 1, "다른 기기를 밀어내며 다시 등록하지 않는다");
  assert.equal(state.subscription, null);
});

// --- 리뷰 P2: 알림을 켜면서 다른 탭의 옛 파일을 지우지 않게 ------------------------------------
test("지금 워커가 푸시를 처리하면 업데이트 · 교체를 건드리지 않는다", async (t) => {
  signIn(t, 7);
  t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  const waiting = fakeWorker({ state: "installed", answers: { "ggp:skip-waiting": { skipping: true } } });
  const { win, state } = fakeBrowser({ active: fakeWorker({ answers: PUSHES }), waiting });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(state.updates, 0);
  assert.deepEqual(waiting.asked, [], "기다리는 새 워커(새 배포)를 억지로 올리지 않는다");
});

test("옛 워커(푸시 처리 없음)에 다른 창이 있어 새 워커로 못 바꾸면 — 켜지 않고 '다른 창을 닫고 다시'", async (t) => {
  signIn(t, 7);
  const put = t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  const waiting = fakeWorker({ state: "installed", answers: { "ggp:skip-waiting": { skipping: false } } });
  const { win, state } = fakeBrowser({ active: fakeWorker(), waiting });
  const started = Date.now();
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "update-pending");
  assert.equal(state.updates, 1);
  assert.deepEqual(waiting.asked, ["ggp:skip-waiting"]);
  assert.equal(waiting.state, "installed", "다른 창의 옛 파일을 지키려 새 워커는 기다린다");
  assert.equal(state.subscription, null, "알림을 띄우지 못하는 옛 워커에 구독을 걸지 않는다");
  assert.equal(put.mock.callCount(), 0);
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), null);
  assert.ok(Date.now() - started < 4_000, "거절하면 8초를 기다리지 않는다");
  assert.equal(enableToast("update-pending").body, "열려 있는 다른 껄무새 창을 닫고 다시 켜 주세요.");
});

test("옛 워커라도 열린 창이 이것 하나면 새 워커로 바꾸고, 새 워커가 푸시를 처리하는 걸 확인한 뒤 켠다", async (t) => {
  signIn(t, 7);
  t.mock.method(api, "pushSubscribe", async () => ({ subscribed: true }));
  const waiting = fakeWorker({ state: "installed", answers: { "ggp:skip-waiting": { skipping: true }, ...PUSHES } });
  const { win, state } = fakeBrowser({ active: fakeWorker(), waiting });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.deepEqual(waiting.asked, ["ggp:skip-waiting", "ggp:ping"]);
  assert.ok(state.subscription);
});

test("설정을 처음에 못 읽으면 '실패'로 표시하고, 다음에 부를 때 다시 읽는다", async (t) => {
  let calls = 0;
  t.mock.method(api, "pushConfig", async () => {
    calls += 1;
    if (calls === 1) throw new Error("offline");
    return { enabled: true, public_key: "AQID" };
  });
  const first = await loadPushConfig({ refresh: true });
  assert.deepEqual(first, { enabled: false, public_key: "", failed: true }, "서버가 꺼 둔 것(enabled:false)과 구분한다");
  assert.deepEqual(await loadPushConfig(), { enabled: true, public_key: "AQID" });
  assert.deepEqual(await loadPushConfig(), { enabled: true, public_key: "AQID" });
  assert.equal(calls, 2, "읽은 뒤에는 같이 쓴다");
});

// --- 리뷰 P2: 시험 알림 결과 · 첫 묻기 -----------------------------------------------------
test("시험 알림 — 0건은 '보냈어요'가 아니다(잠깐 막힘 · 끊긴 기기를 가른다)", () => {
  assert.deepEqual(testToast({ sent: 2, devices: 2, retry: 0 }), { tone: "done", label: "알림", title: "시험 알림을 보냈어요", body: "기기 2대" });
  assert.equal(testToast({ sent: 1, devices: 2, retry: 0 }).body, "기기 2대 중 1대");
  const blocked = testToast({ sent: 0, devices: 1, retry: 1 });
  assert.equal(blocked.tone, "error");
  assert.match(blocked.body, /잠시 뒤에 다시 보내/);
  const dead = testToast({ sent: 0, devices: 1, retry: 0 });
  assert.equal(dead.tone, "error");
  assert.match(dead.body, /알림을 다시 켜/);
});

// --- 리뷰 P2: 오프라인 안내 화면에서 알림을 눌렀을 때 · 워커 교체 조건 --------------------------
function workerHarness({ windows = [] } = {}) {
  const hooks = new Map();
  const opened = [];
  const shown = [];
  let skipped = 0;
  const source = readFileSync(new URL("../build/serviceWorker.js", import.meta.url), "utf8")
    .replace("__CACHE_VERSION__", "fixture")
    .replace("__BUILD_ASSETS__", "[]");
  vm.runInNewContext(source, {
    URL, Set, Promise, Error, MessageChannel, clearTimeout,
    setTimeout: (fn, ms) => setTimeout(fn, Math.min(ms, 100)),
    self: {
      location: { origin: "https://ggparrot.test" },
      registration: { showNotification: async (title, options) => { shown.push({ title, ...options }); } },
      clients: { claim: async () => {}, matchAll: async () => windows, openWindow: async (url) => { opened.push(url); return {}; } },
      skipWaiting: async () => { skipped += 1; },
      addEventListener: (name, fn) => hooks.set(name, fn),
    },
  });
  return {
    opened, shown, skipped: () => skipped,
    async fire(name, event) {
      const pending = [];
      hooks.get(name)({ ...event, waitUntil: (promise) => pending.push(promise) });
      await Promise.all(pending);
    },
  };
}

function fakeWindow({ url = "https://ggparrot.test/news", listens = true, controlled = true, focused = false } = {}) {
  const log = [];
  return {
    url, focused, visibilityState: "visible", log,
    focus: async () => { log.push("focus"); },
    postMessage: (message, ports) => {
      log.push(["message", message.url]);
      if (listens) ports?.[0]?.postMessage({ opened: true }); // usePushBridge 가 옮기고 답한다
    },
    navigate: async (path) => {
      log.push(["navigate", path]);
      if (!controlled) throw new TypeError("not controlled");
      return {};
    },
  };
}

const click = (url) => ({ notification: { close() {}, data: { url } } });

test("알림을 누르면 — 앱 화면은 그 자리에서 옮기고, 답이 없는 화면(오프라인 안내)은 그 주소로 다시 연다", async () => {
  const app = fakeWindow();
  const h = workerHarness({ windows: [app] });
  await h.fire("notificationclick", click("/agents?session=4"));
  assert.deepEqual(app.log, ["focus", ["message", "/agents?session=4"]]);
  assert.deepEqual(h.opened, []);

  const offline = fakeWindow({ listens: false });
  const h2 = workerHarness({ windows: [offline] });
  await h2.fire("notificationclick", click("/agents?session=4"));
  assert.deepEqual(offline.log.at(-1), ["navigate", "/agents?session=4"], "받는 코드가 없는 화면은 그 창을 그 주소로");
  assert.deepEqual(h2.opened, []);

  const stranger = fakeWindow({ listens: false, controlled: false });
  const h3 = workerHarness({ windows: [stranger] });
  await h3.fire("notificationclick", click("/agents?session=4"));
  assert.deepEqual(h3.opened, ["/agents?session=4"], "이 워커가 맡지 않은 창이면 새 창으로");

  const h4 = workerHarness();
  await h4.fire("notificationclick", click("https://evil.example/x"));
  assert.deepEqual(h4.opened, ["/"], "다른 사이트 주소는 첫 화면으로");
});

test("알림에 일어난 시각을 찍는다 — 다시 보내기로 늦게 와도 매수 · 매도 순서를 헷갈리지 않게", async () => {
  const h = workerHarness();
  const push = (payload) => ({ data: { json: () => payload, text: () => JSON.stringify(payload) } });
  await h.fire("push", push({ title: "LTC · 샀어요!", body: "100.00 USDT", url: "/agents?session=4", tag: "s4:fill:1", at: 1_762_400_000_000 }));
  await h.fire("push", push({ title: "껄무새 앱 알림이 켜졌어요", url: "/agents" }));
  assert.equal(h.shown[0].timestamp, 1_762_400_000_000);
  assert.equal(h.shown[0].tag, "s4:fill:1");
  assert.equal("timestamp" in h.shown[1], false, "시각이 없는 알림(시험 알림)은 받은 시각 그대로");
});

test("워커 — ping 에 답하고, 열린 창이 둘 이상이면 새 워커로 바로 바꾸지 않는다", async () => {
  const ask = async (h, type) => {
    const channel = new MessageChannel();
    const answer = new Promise((resolve) => { channel.port1.onmessage = (event) => resolve(event.data); });
    await h.fire("message", { data: { type }, ports: [channel.port2] });
    const value = await answer;
    channel.port1.close();
    return value;
  };
  const single = workerHarness({ windows: [fakeWindow()] });
  assert.deepEqual(await ask(single, "ggp:ping"), { push: true });
  assert.deepEqual(await ask(single, "ggp:skip-waiting"), { skipping: true });
  assert.equal(single.skipped(), 1);
  const busy = workerHarness({ windows: [fakeWindow(), fakeWindow({ url: "https://ggparrot.test/agents" })] });
  assert.deepEqual(await ask(busy, "ggp:skip-waiting"), { skipping: false });
  assert.equal(busy.skipped(), 0, "옛 파일로 도는 다른 창의 캐시를 지우지 않는다");
});

// --- 설치 안내(시안 B안) · 처음 묻기 ------------------------------------------------
import { installGuide, iosVersion } from "../src/lib/pwaGuide.js";
import { shouldAskPush } from "../src/lib/push.js";

const text = (parts) => parts.map((part) => (typeof part === "string" ? part : part.b || `[${part.icon}]`)).join("");

test("안드로이드는 설치 창을 띄울 수 있으면 버튼 하나, 못 띄우면 크롬 · 삼성 인터넷 3단계", () => {
  assert.deepEqual(installGuide({ tab: "android", platform: "android", browser: "chrome", canPrompt: true }),
    { kind: "android", button: "only", steps: [], note: "" });
  const chrome = installGuide({ tab: "android", platform: "android", browser: "chrome", canPrompt: false });
  assert.equal(chrome.button, null);
  assert.deepEqual(chrome.steps.map((s) => s.title), ["⋮ 누르기", "앱 설치", "설치"]);
  assert.equal(chrome.steps[0].hint, null);
  const samsung = installGuide({ tab: "android", platform: "android", browser: "samsung", canPrompt: false });
  assert.deepEqual(samsung.steps.map((s) => s.title), ["메뉴", "현재 페이지 추가", "홈 화면"]);
  const fromPc = installGuide({ tab: "android", platform: "desktop", browser: "chrome", canPrompt: true, host: "gg-parrot.vercel.app" });
  assert.equal(fromPc.button, null, "PC 에서 안드로이드 안내를 볼 때는 버튼이 없다");
  assert.equal(text(fromPc.steps[0].hint), "휴대폰 크롬에서 gg-parrot.vercel.app 을 먼저 열어요.");
});

test("PC 는 앱 설치 안내 · 앱 알림이 없다 — 데스크톱 실행기가 알림을 보낸다", () => {
  const pc = { navigator: { userAgent: UA.windowsEdge, platform: "Win32", maxTouchPoints: 0 }, matchMedia: () => ({ matches: false }) };
  assert.equal(pushSupport(pc), "desktop");
  assert.equal(offersAppInstall({ platform: "desktop", standalone: false }), false);
  assert.equal(offersAppInstall({ platform: "ios", standalone: false }), true, "아이패드(터치 되는 맥)도 휴대폰과 같이");
  assert.equal(offersAppInstall({ platform: "android", standalone: true }), false, "이미 앱으로 열었으면 없다");
});

test("아이폰은 사파리 26 이상 ⋯ › 공유, 그 아래는 공유 버튼, 아이폰 크롬은 주소창 공유 — 끝에 알림 안내", () => {
  assert.equal(iosVersion(UA.iphone), 26);
  assert.equal(iosVersion("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"), 17);
  const new26 = installGuide({ tab: "ios", platform: "ios", browser: "safari", version: 26 });
  assert.deepEqual(new26.steps.map((s) => s.title), ["⋯ 누르고 공유", "홈 화면에 추가", "추가"]);
  assert.equal(text(new26.steps[2].text), "웹 앱으로 열기가 켜진 채로 추가");
  assert.equal(new26.note, "push");
  const old17 = installGuide({ tab: "ios", platform: "ios", browser: "safari", version: 17 });
  assert.equal(old17.steps[0].title, "공유 버튼");
  assert.equal(installGuide({ tab: "ios", platform: "ios", browser: "ios-other", version: 0 }).steps[0].title, "공유 버튼");
});

test("카카오톡 · 네이버 앱 안에서는 먼저 브라우저로", () => {
  const inApp = installGuide({ tab: "android", platform: "android", browser: "in-app", canPrompt: false });
  assert.equal(inApp.kind, "in-app");
  assert.deepEqual(inApp.steps.map((s) => s.title), ["⋮ 누르기", "다른 브라우저로 열기", "앱 설치"]);
});

test("처음 묻기는 로그인했고 · 앱으로 열었고 · 꺼져 있고 · 권한을 안 물었고 · 답한 적 없을 때만", () => {
  const base = { signedIn: true, standalone: true, state: "off", permission: "default", alreadyAsked: false, path: "/agents" };
  assert.equal(shouldAskPush(base), true);
  assert.equal(shouldAskPush({ ...base, signedIn: false }), false, "로그인 전에는 묻지 않는다 — 켤 계정이 없어 답만 써 버린다");
  assert.equal(shouldAskPush({ ...base, standalone: false }), false, "브라우저 탭에서는 묻지 않는다");
  assert.equal(shouldAskPush({ ...base, state: "on" }), false);
  assert.equal(shouldAskPush({ ...base, permission: "denied" }), false);
  assert.equal(shouldAskPush({ ...base, alreadyAsked: true }), false, "나중에 · 받기 둘 다 한 번 답하면 끝");
  assert.equal(shouldAskPush({ ...base, path: "/login" }), false);
});
