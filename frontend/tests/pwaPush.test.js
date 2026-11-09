import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { api } from "../src/api.js";
import { clearAuth, getToken, setAuth } from "../src/lib/auth.js";
import { detectBrowser, detectPlatform, offersAppInstall, promptInstall, installState, resetInstallStateForTest } from "../src/lib/pwa.js";
import * as push from "../src/lib/push.js";
import { createPushController } from "../src/lib/pushController.js";
import { closeAppInstall, openAppInstallFromLink } from "../src/lib/installDialog.js";
import { enableToast, testToast } from "../src/hooks/usePushState.js";

const {
  disablePush, enablePush, forgetPushStatus, loadPushConfig, openTarget, pushSupport, readPushState, readRecord,
  setPushTimingForTest, syncPush, urlBase64ToUint8Array,
} = push;

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
    getSubscription: async () => {
      if (state.onRead) await state.onRead(); // 시험이 구독 읽기 도중을 붙잡을 때
      return state.subscription;
    },
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

// 서버 흉내 — 백엔드 push_alerts 와 같은 규칙: 엔드포인트마다 (계정, 세대). 다른 계정이 켜면 세대가 오르고,
// 기대한 세대가 다르면 재등록은 409 · 해제는 그대로 둔다. 요청한 계정은 그 순간의 토큰으로 정한다(api.js 와 같이).
function fakeServer(t, { delay = null } = {}) {
  const rows = new Map();
  const accepted = [];
  let generation = 100;
  const caller = () => {
    const token = getToken();
    return token ? Number(token.split("-")[1]) : null;
  };
  const wait = async () => {
    if (delay) await delay();
  };
  t.mock.method(api, "pushSubscribe", async (body) => {
    const user = caller(); // 보내는 순간의 토큰
    await wait();
    const row = rows.get(body.endpoint);
    if (body.expected_version != null) {
      if (!row || row.user !== user || row.version !== body.expected_version) {
        const error = new Error("conflict");
        error.status = 409;
        throw error;
      }
      accepted.push({ user, endpoint: body.endpoint, kind: "sync" });
      return { subscribed: true, version: row.version };
    }
    const version = !row || row.user !== user ? (generation += 1) : row.version;
    rows.set(body.endpoint, { user, version });
    accepted.push({ user, endpoint: body.endpoint, kind: "enable" });
    return { subscribed: true, version };
  });
  t.mock.method(api, "pushUnsubscribe", async (endpoint, expected) => {
    const user = caller();
    await wait();
    const row = rows.get(endpoint);
    if (row && row.user === user && (expected == null || expected === row.version)) rows.delete(endpoint);
    return { subscribed: false };
  });
  t.mock.method(api, "pushStatus", async (endpoint) => {
    const user = caller();
    await wait();
    const row = rows.get(endpoint);
    return row && row.user === user ? { subscribed: true, version: row.version } : { subscribed: false, version: 0 };
  });
  return { rows, accepted };
}

test("켜기 → 서버가 받은 세대까지 기록 · 켜짐, 끄기 → 그 세대만 해제 · 브라우저 구독 끊기", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser();
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "off");
  assert.equal(await readPushState({ userId: 7, config: { enabled: false }, win }), "unconfigured");
  assert.equal(await readPushState({ userId: 7, config: { enabled: false, failed: true }, win }), "unknown",
    "설정을 못 읽은 것은 '꺼짐'이 아니다");
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(state.permissionAsked, 1);
  const endpoint = state.subscription.endpoint;
  assert.deepEqual(readRecord(), { v: 2, user: "7", endpoint, version: server.rows.get(endpoint).version,
    syncedAt: readRecord().syncedAt });
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(await readPushState({ userId: 8, config: CONFIG, win }), "off", "다른 계정에게는 꺼진 것");
  assert.equal(await disablePush({ win }), "off");
  assert.equal(server.rows.has(endpoint), false);
  assert.equal(api.pushUnsubscribe.mock.calls[0].arguments[1], 101, "해제는 기록의 세대를 함께 보낸다");
  assert.equal(state.unsubscribed, 1);
  assert.equal(readRecord(), null);
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "off");
});

test("권한을 거절하면 구독하지 않는다", async (t) => {
  signIn(t, 7);
  fakeServer(t);
  const { win, state } = fakeBrowser({ answer: "denied" });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "denied");
  assert.equal(state.subscription, null);
  assert.equal(api.pushSubscribe.mock.callCount(), 0);
  win.Notification.permission = "denied";
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "denied");
});

test("다른 계정이 켜 둔 구독은 로그인한 사람에게 옮기지 않고 끊는다 · 12시간 재등록은 서버의 세대를 기대한다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  assert.equal(await syncPush({ userId: 7, win }), "fresh", "방금 맞춘 기기는 다시 묻지 않는다");
  assert.equal(await syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 }), "synced");
  assert.equal(api.pushSubscribe.mock.calls[1].arguments[0].expected_version, server.rows.get(state.subscription.endpoint).version);
  clearAuth();
  setAuth("token-9", { id: 9 });
  assert.equal(await syncPush({ userId: 9, win }), "dropped");
  assert.equal(state.unsubscribed, 1);
  assert.equal(api.pushUnsubscribe.mock.callCount(), 0, "로그아웃 뒤 정리는 서버에 묻지 않는다(410 으로 지워진다)");
  assert.equal(server.accepted.filter((item) => item.user === 9).length, 0, "9번 계정으로 옮기지 않는다");
});

test("재등록 직전 다른 계정이 서버에서 이 기기를 가져갔으면(409) — 되살리지 않고 이 기기 구독도 끊는다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  const endpoint = state.subscription.endpoint;
  const real = api.pushStatus;
  t.mock.method(api, "pushStatus", async (value) => {
    const answer = await real(value);
    server.rows.set(endpoint, { user: 8, version: 999 }); // 서버가 답한 직후 다른 계정이 가져갔다
    return answer;
  });
  assert.equal(await syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 }), "dropped");
  assert.deepEqual(server.rows.get(endpoint), { user: 8, version: 999 }, "옛 세대를 기대한 재등록은 서버가 거절했다");
  assert.equal(state.subscription, null);
  assert.equal(readRecord(), null);
});

// --- 계정 전환 · 늦은 답 · 두 탭 -------------------------------------------------------------------
test("켜는 도중 다른 탭에서 B 로 로그인하면 — 그만두고, 그사이 만든 구독도 끊는다(B 로 등록하지 않는다)", async (t) => {
  signIn(t, 7);
  fakeServer(t);
  const { win, state } = fakeBrowser({ beforeSubscribe: async () => {
    clearAuth();
    setAuth("token-8", { id: 8 });
  } });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "cancelled");
  assert.equal(api.pushSubscribe.mock.callCount(), 0, "B 의 토큰으로 서버에 등록하지 않는다");
  assert.equal(state.subscription, null, "그사이 만든 구독은 끊는다");
  assert.equal(readRecord(), null, "A 를 켠 계정으로 적지 않는다");
  assert.equal(await readPushState({ userId: 8, config: CONFIG, win }), "off");
  assert.equal(enableToast("cancelled"), null, "토스트도 띄우지 않는다");
});

test("서버 등록 응답을 기다리는 동안 계정이 바뀌어도 — 기록을 남기지 않고 구독을 끊는다 · 등록 중에는 켜짐이 아니다", async (t) => {
  signIn(t, 7);
  let release;
  const held = new Promise((resolve) => { release = resolve; });
  let duringPut = null;
  const { win, state } = fakeBrowser();
  fakeServer(t, { delay: async () => {
    if (release) {
      duringPut = { record: readRecord(), state: await readPushState({ userId: 7, config: CONFIG, win }) };
      clearAuth();
      setAuth("token-8", { id: 8 });
      const go = release;
      release = null;
      go();
    }
    await held;
  } });
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "cancelled");
  assert.equal(duringPut.record.pending, true, "등록 중 기록");
  assert.equal(duringPut.state, "off", "서버가 받기 전에는 켜짐이 아니다");
  assert.equal(state.subscription, null);
  assert.equal(readRecord(), null);
});

test("서버가 이 기기를 지웠으면 '꺼짐' — 같이 읽어도 서버에는 한 번 묻고, 다시 켜면 새 구독으로 등록한다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win } = fakeBrowser();
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
  assert.equal(api.pushStatus.mock.callCount(), 0, "방금 켠 기기는 서버에 다시 묻지 않는다");
  forgetPushStatus();
  server.rows.clear(); // 11번째 기기로 밀림 · 끝난 구독(410) · 거절 반복
  const reads = await Promise.all([1, 2, 3].map(() => readPushState({ userId: 7, config: CONFIG, win })));
  assert.deepEqual(reads, ["off", "off", "off"]);
  assert.equal(api.pushStatus.mock.callCount(), 1, "설정 · 단추 · 첫 묻기가 같이 읽어도 서버에는 한 번");
  assert.ok(readRecord(), "읽기는 저장소를 건드리지 않는다");
  assert.equal(await enablePush({ userId: 7, config: CONFIG, win }), "on");
  const puts = api.pushSubscribe.mock.calls.map((call) => call.arguments[0].endpoint);
  assert.equal(puts.length, 2);
  assert.notEqual(puts[1], puts[0], "남은 구독을 다시 쓰지 않고 새 구독으로");
});

test("서버에 못 물으면 '확인 못 함' — 꺼짐으로 보이지 않고, 동기화는 다음에", async (t) => {
  signIn(t, 7);
  fakeServer(t);
  const { win } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  forgetPushStatus();
  t.mock.method(api, "pushStatus", async () => { throw new Error("offline"); });
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "unknown");
  assert.equal(await syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 }), "unverified");
  assert.ok(readRecord(), "못 물었다고 기록을 지우지 않는다");
});

test("A 의 켜기가 워커를 기다리는 사이 B 로 로그인해 켜면 — A 는 아무 구독도 건드리지 않고 그만둔다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  let held;
  let pings = 0;
  const reached = new Promise((resolve) => { held = resolve; });
  const active = fakeWorker({ onPing: (port) => {
    pings += 1;
    if (pings === 1) held(port);
    else port.postMessage({ push: true });
  } });
  const { win, state } = fakeBrowser({ active });
  const enableA = enablePush({ userId: 7, config: CONFIG, win });
  const port = await reached;
  clearAuth();
  setAuth("token-8", { id: 8 });
  const enableB = enablePush({ userId: 8, config: CONFIG, win });
  port.postMessage({ push: true });
  assert.equal(await enableA, "cancelled");
  assert.equal(await enableB, "on", "B 는 A 가 끝난 뒤 차례로 켠다");
  assert.ok(state.subscription);
  assert.equal(readRecord().user, "8");
  assert.deepEqual(server.accepted.map((item) => item.user), [8], "서버 등록은 B 한 번");
  assert.equal(state.unsubscribed, 0, "A 가 끊은 구독이 없다");
});

test("12시간 동기화의 마지막 구독 읽기 도중 A→B 로 바뀌면 — B 의 토큰으로 A 의 구독을 등록하지 않는다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  let pauseNext = false;
  let entered;
  let resume;
  const reached = new Promise((resolve) => { entered = resolve; });
  const gate = new Promise((resolve) => { resume = resolve; });
  state.onRead = async () => {
    if (!pauseNext) return;
    pauseNext = false;
    entered();
    await gate;
  };
  const real = api.pushStatus;
  t.mock.method(api, "pushStatus", async (endpoint) => {
    pauseNext = true; // 서버 답 뒤의 '마지막 구독 읽기'를 붙잡는다
    return real(endpoint);
  });
  const sync = syncPush({ userId: 7, win, now: Date.now() + 13 * 3600_000 });
  await reached;
  clearAuth();
  setAuth("token-8", { id: 8 });
  resume();
  assert.equal(await sync, "cancelled");
  assert.deepEqual(server.accepted.map((item) => item.user), [7], "켤 때 한 번뿐 — B 의 토큰으로 다시 등록하지 않는다");
});

test("두 탭: 탭1 의 켜기가 늦게 실패해도 탭2 가 그사이 켠 새 구독과 그 기록은 남는다(Web Locks · 없을 때 모두)", async (t) => {
  const tab2 = await import("../src/lib/push.js?tab=2"); // 탭마다 따로 도는 모듈 — 구독 · 저장소만 같다
  for (const locks of [true, false]) {
    signIn(t, 7);
    tab2.forgetPushStatus();
    tab2.setPushTimingForTest({ ping: 40, finalPing: 40 });
    if (!locks) Object.defineProperty(globalThis.navigator, "locks", { value: undefined, configurable: true });
    try {
      let fail;
      let asked;
      const reached = new Promise((resolve) => { asked = resolve; });
      let calls = 0;
      fakeServer(t);
      const real = api.pushSubscribe;
      t.mock.method(api, "pushSubscribe", (body) => {
        calls += 1;
        if (calls > 1) return real(body);
        asked();
        return new Promise((_, reject) => { fail = reject; }); // 탭1 의 서버 등록이 오래 걸린다
      });
      const { win, state } = fakeBrowser({ permission: "granted" });
      const first = enablePush({ userId: 7, config: CONFIG, win }).catch((error) => error.message);
      await reached;
      const second = tab2.enablePush({ userId: 7, config: CONFIG, win });
      if (!locks) assert.equal(await second, "on", "잠금이 없으면 탭2 가 먼저 끝난다");
      fail(new Error("tab1 registration failed"));
      assert.equal(await first, "tab1 registration failed");
      assert.equal(await second, "on");
      const endpoint = state.subscription?.endpoint;
      assert.ok(endpoint, `탭2 의 구독이 남는다(locks=${locks})`);
      assert.equal(readRecord()?.endpoint, endpoint, "기록도 탭2 구독 것");
      assert.equal(await tab2.syncPush({ userId: 7, win }), "fresh");
      assert.equal(state.subscription?.endpoint, endpoint, "다음 동기화가 끊지 않는다");
    } finally {
      if (!locks) delete globalThis.navigator.locks;
      tab2.setPushTimingForTest();
      t.mock.restoreAll();
    }
  }
  assert.equal(typeof globalThis.navigator.locks?.request, "function", "잠금을 되돌려 놓았다");
});

// --- 저장 형식 이전 -------------------------------------------------------------------------------
test("이전 형식 기록(v1 키 셋) — 서버가 이 계정으로 확인해 주면 세대를 받아 옮기고 켜짐을 지킨다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser({ permission: "granted" });
  const subscription = await win.navigator.serviceWorker.ready.then((reg) => reg.pushManager.subscribe({ applicationServerKey: new Uint8Array(1) }));
  server.rows.set(subscription.endpoint, { user: 7, version: 55 });
  globalThis.localStorage.setItem("ggp_push_owner", "7");
  globalThis.localStorage.setItem("ggp_push_owner_endpoint", subscription.endpoint);
  globalThis.localStorage.setItem("ggp_push_synced_at", String(Date.now()));
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "unknown", "옮기기 전에는 켜짐 · 꺼짐을 단정하지 않는다");
  assert.equal(await syncPush({ userId: 7, win }), "migrated");
  assert.deepEqual({ ...readRecord(), syncedAt: 0 }, { v: 2, user: "7", endpoint: subscription.endpoint, version: 55, syncedAt: 0 });
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), null, "옛 키는 지운다");
  assert.equal(state.unsubscribed, 0, "정상 구독을 끊지 않는다");
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
});

test("이전 형식 기록 — 서버에 없으면 정리하고, 서버에 못 물으면 옛 기록을 두고 다음에", async (t) => {
  signIn(t, 7);
  fakeServer(t);
  const { win, state } = fakeBrowser({ permission: "granted" });
  const subscription = await win.navigator.serviceWorker.ready.then((reg) => reg.pushManager.subscribe({ applicationServerKey: new Uint8Array(1) }));
  globalThis.localStorage.setItem("ggp_push_owner", "7");
  const real = api.pushStatus;
  t.mock.method(api, "pushStatus", async () => { throw new Error("offline"); });
  assert.equal(await syncPush({ userId: 7, win }), "unverified");
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), "7", "못 물었으면 그대로");
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "unknown", "꺼짐이 아니라 확인 못 함");
  t.mock.method(api, "pushStatus", real);
  forgetPushStatus();
  assert.equal(await syncPush({ userId: 7, win }), "dropped");
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), null);
  assert.equal(state.subscription, null, "서버에 없는 옛 구독은 끊는다");
  assert.ok(subscription.endpoint);
});

// --- 상태 하나(PushController) — 탭들이 같은 결론에 닿는다 ---------------------------------------------
async function until(check, ms = 1_000) {
  const end = Date.now() + ms;
  while (!check()) {
    if (Date.now() > end) throw new Error("timeout");
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

test("한 탭에서 켜고 끄면 다른 탭도 서버 확인을 새로 하고 같은 상태가 된다 · 등록 중에는 켜짐이 아니다", async (t) => {
  signIn(t, 7);
  const tab2ops = await import("../src/lib/push.js?controller-tab=2");
  tab2ops.setPushTimingForTest({ ping: 40, finalPing: 40 });
  let release = null;
  fakeServer(t, { delay: () => (release ? new Promise((resolve) => { release = resolve; }) : null) });
  const { win } = fakeBrowser();
  const one = createPushController({ ...push, loadPushConfig: async () => CONFIG }, { win, doc: null });
  const two = createPushController({ ...tab2ops, loadPushConfig: async () => CONFIG }, { win, doc: null });
  t.after(() => {
    one.dispose();
    two.dispose();
    tab2ops.setPushTimingForTest();
  });
  one.setUser("7");
  two.setUser("7");
  await until(() => one.snapshot().state === "off" && two.snapshot().state === "off");
  release = () => {};
  const enabling = one.enable();
  await until(() => typeof release === "function" && release.length === 1);
  assert.equal(one.snapshot().busy, true);
  assert.equal(one.snapshot().state, "off", "서버가 받기 전에는 켜짐으로 바꾸지 않는다");
  const go = release;
  release = null;
  go();
  assert.equal(await enabling, "on");
  assert.equal(one.snapshot().state, "on");
  await until(() => two.snapshot().state === "on");
  await one.disable();
  await until(() => two.snapshot().state === "off");
});
// --- 맞추기(reconcile) — 앱 재시작 · 연결 복구에서도 상태가 맞는다 ----------------------------------------
test("켜는 도중 앱이 닫혀 '등록 중' 기록만 남았으면 — 다음 시작 때 서버에 물어 켜짐으로 확정하거나 정리한다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser({ permission: "granted" });
  const subscription = await win.navigator.serviceWorker.ready.then((reg) => reg.pushManager.subscribe({ applicationServerKey: new Uint8Array(1) }));
  // 서버는 등록을 받았지만, 기록을 확정하기 전에 앱이 닫혔다(이 탭에는 그 켜기가 돌고 있지 않다).
  server.rows.set(subscription.endpoint, { user: 7, version: 321 });
  globalThis.localStorage.setItem("ggp_push_device", JSON.stringify({ v: 2, user: "7", endpoint: subscription.endpoint,
    version: 0, syncedAt: 0, pending: true, op: "gone-op", startedAt: Date.now() }));
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "unknown", "확정 전에는 켜짐도 꺼짐도 아니다");
  assert.equal(await syncPush({ userId: 7, win }), "confirmed");
  assert.deepEqual({ ...readRecord(), syncedAt: 0 }, { v: 2, user: "7", endpoint: subscription.endpoint, version: 321, syncedAt: 0 });
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on");
  // 서버에 없는 등록 중 기록은 정리한다
  server.rows.clear();
  globalThis.localStorage.setItem("ggp_push_device", JSON.stringify({ v: 2, user: "7", endpoint: subscription.endpoint,
    version: 0, syncedAt: 0, pending: true, op: "gone-op-2", startedAt: Date.now() }));
  forgetPushStatus();
  assert.equal(await syncPush({ userId: 7, win }), "dropped");
  assert.equal(readRecord(), null);
  assert.equal(state.subscription, null);
});

test("다른 탭에서 진행 중인 켜기의 '등록 중' 기록은 건드리지 않는다 — 잠금이 없는 브라우저도 1분 안의 기록은 기다린다", async (t) => {
  const tab2 = await import("../src/lib/push.js?pending-tab=2");
  signIn(t, 7);
  tab2.forgetPushStatus();
  let release = null;
  let held = false;
  const { win } = fakeBrowser({ permission: "granted" });
  fakeServer(t, { delay: async () => {
    if (!held) {
      held = true;
      await new Promise((resolve) => { release = resolve; });
    }
  } });
  Object.defineProperty(globalThis.navigator, "locks", { value: undefined, configurable: true });
  try {
    const enabling = enablePush({ userId: 7, config: CONFIG, win }); // 탭1 의 켜기 — 서버 등록 응답을 기다리는 중
    while (!release) await new Promise((resolve) => setTimeout(resolve, 1));
    assert.equal(readRecord().pending, true);
    assert.equal(await tab2.syncPush({ userId: 7, win }), "busy", "탭2 는 1분 안의 등록 중 기록을 기다린다");
    assert.equal(readRecord().pending, true, "건드리지 않았다");
    release();
    assert.equal(await enabling, "on");
    assert.equal(readRecord().pending, undefined);
  } finally {
    delete globalThis.navigator.locks;
  }
});

test("남은 '등록 중' 기록을 확정하려다 서버에 못 물으면 — 화면은 꺼짐이 아니라 '확인 못 함', 연결이 돌아오면 켜짐", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win } = fakeBrowser({ permission: "granted" });
  const events = new EventTarget();
  win.addEventListener = events.addEventListener.bind(events);
  const subscription = await win.navigator.serviceWorker.ready.then((reg) => reg.pushManager.subscribe({ applicationServerKey: new Uint8Array(1) }));
  server.rows.set(subscription.endpoint, { user: 7, version: 9 });
  globalThis.localStorage.setItem("ggp_push_device", JSON.stringify({ v: 2, user: "7", endpoint: subscription.endpoint,
    version: 0, syncedAt: 0, pending: true, op: "closed-app", startedAt: Date.now() }));
  const real = api.pushStatus;
  t.mock.method(api, "pushStatus", async () => { throw new Error("offline"); });
  const controller = createPushController({ ...push, loadPushConfig: async () => CONFIG }, { win, doc: null });
  t.after(() => controller.dispose());
  controller.setUser("7");
  await until(() => api.pushStatus.mock.callCount() >= 1 && controller.snapshot().state !== "loading");
  assert.equal(controller.snapshot().state, "unknown", "실제로는 켜진 구독 — 꺼짐으로 보이면 안 된다");
  await controller.refresh();
  assert.equal(controller.snapshot().state, "unknown", "따로 읽어도 꺼짐으로 덮지 않는다");
  t.mock.method(api, "pushStatus", real);
  push.forgetPushStatus();
  events.dispatchEvent(new Event("online"));
  await until(() => controller.snapshot().state === "on");
  assert.equal(readRecord().version, 9);
});

test("서버에 못 물은 답은 캐시하지 않는다 — 연결이 돌아온 직후 확인은 바로 서버에 다시 묻는다 · 옮기기는 한 번만 묻는다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  forgetPushStatus();
  const real = api.pushStatus;
  t.mock.method(api, "pushStatus", async () => { throw new Error("offline"); });
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "unknown");
  t.mock.method(api, "pushStatus", real);
  assert.equal(await readPushState({ userId: 7, config: CONFIG, win }), "on", "15초 안이어도 실패는 다시 묻는다");
  // 이전 형식 옮기기 — 같은 주소 · 세대를 서버에 한 번만 묻는다
  signIn(t, 7);
  const again = fakeServer(t);
  const fresh = fakeBrowser({ permission: "granted" });
  const subscription = await fresh.win.navigator.serviceWorker.ready.then((reg) => reg.pushManager.subscribe({ applicationServerKey: new Uint8Array(1) }));
  again.rows.set(subscription.endpoint, { user: 7, version: 12 });
  globalThis.localStorage.setItem("ggp_push_owner", "7");
  globalThis.localStorage.setItem("ggp_push_owner_endpoint", subscription.endpoint);
  assert.equal(await syncPush({ userId: 7, win: fresh.win }), "migrated");
  assert.equal(api.pushStatus.mock.callCount(), 1, "상태 확인 한 번");
  assert.ok(server);
});

test("서버가 계속 503 이어도 — 시작 확인은 상태를 한 번만 묻고, 연결 복구 · 돌아옴이 몰려도 도는 것 뒤에 한 번만 더 묻는다", async (t) => {
  signIn(t, 7);
  fakeServer(t);
  const { win } = fakeBrowser();
  const events = new EventTarget();
  win.addEventListener = events.addEventListener.bind(events);
  const doc = new EventTarget();
  doc.visibilityState = "visible";
  await enablePush({ userId: 7, config: CONFIG, win });
  forgetPushStatus();
  const real = api.pushStatus;
  let gate = null;
  t.mock.method(api, "pushStatus", async () => {
    if (gate) await gate;
    throw Object.assign(new Error("busy"), { status: 503 });
  });
  const controller = createPushController({ ...push, loadPushConfig: async () => CONFIG }, { win, doc });
  t.after(() => controller.dispose());
  controller.setUser("7");
  await until(() => controller.snapshot().state === "unknown");
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(api.pushStatus.mock.callCount(), 1, "맞추기가 못 물었으면 다시 읽을 때 또 묻지 않는다");
  let open;
  gate = new Promise((resolve) => { open = resolve; });
  events.dispatchEvent(new Event("online"));
  await until(() => api.pushStatus.mock.callCount() === 2);
  for (let index = 0; index < 9; index += 1) events.dispatchEvent(new Event("online"));
  for (let index = 0; index < 3; index += 1) doc.dispatchEvent(new Event("visibilitychange"));
  open();
  gate = null;
  await until(() => api.pushStatus.mock.callCount() >= 3);
  await new Promise((resolve) => setTimeout(resolve, 50));
  assert.equal(api.pushStatus.mock.callCount(), 3, "이벤트 13번이 몰려도 도는 중인 것 + 뒤따르는 한 번");
  assert.equal(controller.snapshot().state, "unknown");
  t.mock.method(api, "pushStatus", real);
  events.dispatchEvent(new Event("online"));
  await until(() => controller.snapshot().state === "on");
});

test("서버 세대가 바뀌었으면(같은 계정) 기록을 서버에 맞춘다", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win, state } = fakeBrowser();
  await enablePush({ userId: 7, config: CONFIG, win });
  server.rows.set(state.subscription.endpoint, { user: 7, version: 4242 });
  forgetPushStatus();
  assert.equal(await syncPush({ userId: 7, win }), "adopted");
  assert.equal(readRecord().version, 4242);
  assert.equal(await syncPush({ userId: 7, win }), "fresh");
});

test("이전 형식 기록을 옮기다 오프라인이었으면 — 연결이 돌아오는 것만으로 옮겨 켜짐이 된다(컨트롤러 reconcile)", async (t) => {
  signIn(t, 7);
  const server = fakeServer(t);
  const { win } = fakeBrowser({ permission: "granted" });
  const events = new EventTarget();
  win.addEventListener = events.addEventListener.bind(events);
  const subscription = await win.navigator.serviceWorker.ready.then((reg) => reg.pushManager.subscribe({ applicationServerKey: new Uint8Array(1) }));
  server.rows.set(subscription.endpoint, { user: 7, version: 77 });
  globalThis.localStorage.setItem("ggp_push_owner", "7");
  globalThis.localStorage.setItem("ggp_push_owner_endpoint", subscription.endpoint);
  const real = api.pushStatus;
  t.mock.method(api, "pushStatus", async () => { throw new Error("offline"); });
  const controller = createPushController({ ...push, loadPushConfig: async () => CONFIG }, { win, doc: null });
  t.after(() => controller.dispose());
  controller.setUser("7");
  await until(() => controller.snapshot().state === "unknown" && api.pushStatus.mock.callCount() >= 1);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(controller.snapshot().state, "unknown", "못 물었으면 옮기지 않고 '확인 못 함'으로 기다린다");
  t.mock.method(api, "pushStatus", real);
  events.dispatchEvent(new Event("online"));
  await until(() => controller.snapshot().state === "on");
  assert.equal(readRecord().version, 77);
  assert.equal(globalThis.localStorage.getItem("ggp_push_owner"), null);
});

function mulberry32(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let x = Math.imul(a ^ (a >>> 15), 1 | a);
    x = (x + Math.imul(x ^ (x >>> 7), 61 | x)) ^ x;
    return ((x ^ (x >>> 14)) >>> 0) / 4294967296;
  };
}

test("한꺼번에: 두 탭 · 계정 전환 · 늦은 응답이 섞여도 — 다른 계정으로 등록하지 않고, 끝나면 탭 · 기록 · 서버가 맞는다", async (t) => {
  const tab2 = await import("../src/lib/push.js?random-tab=2");
  const tabs = [push, tab2];
  for (const seed of [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]) {
    const locks = seed % 2 === 0;
    signIn(t, 7);
    tab2.forgetPushStatus();
    tab2.setPushTimingForTest({ ping: 40, finalPing: 40 });
    if (!locks) Object.defineProperty(globalThis.navigator, "locks", { value: undefined, configurable: true });
    const rng = mulberry32(seed);
    try {
      const server = fakeServer(t, { delay: () => new Promise((resolve) => setTimeout(resolve, Math.floor(rng() * 4))) });
      const real = api.pushSubscribe;
      const violations = [];
      t.mock.method(api, "pushSubscribe", (body) => {
        // 규칙 2 — 등록은 그 순간 기기 기록의 계정 토큰으로만 간다(B 의 토큰으로 A 의 구독을 등록하지 않는다).
        const record = readRecord();
        const caller = Number(getToken().split("-")[1]);
        if (!record || record.endpoint !== body.endpoint || Number(record.user) !== caller) {
          violations.push({ body: body.endpoint, record, caller });
        }
        return real(body);
      });
      const { win, state } = fakeBrowser({ permission: "granted" });
      const running = [];
      const who = () => {
        const token = getToken();
        return token ? Number(token.split("-")[1]) : 0;
      };
      for (let step = 0; step < 14; step += 1) {
        const tab = tabs[Math.floor(rng() * 2)];
        const roll = rng();
        const user = who();
        if (roll < 0.3 && user) running.push(tab.enablePush({ userId: user, config: CONFIG, win }).catch(() => "error"));
        else if (roll < 0.45) running.push(tab.disablePush({ win }).catch(() => "error"));
        else if (roll < 0.7 && user) {
          running.push(tab.syncPush({ userId: user, win, now: Date.now() + (rng() < 0.5 ? 13 * 3600_000 : 0) })
            .catch(() => "error"));
        } else if (roll < 0.9) {
          const id = rng() < 0.5 ? 7 : 8; // 다른 탭에서 로그아웃 · 다른 계정으로 로그인
          clearAuth();
          setAuth(`token-${id}`, { id });
        } else {
          running.push(tab.readPushState({ userId: user, config: CONFIG, win }));
        }
        await new Promise((resolve) => setTimeout(resolve, Math.floor(rng() * 3)));
      }
      await Promise.allSettled(running);
      const user = who();
      for (const tab of tabs) await tab.syncPush({ userId: user, win, now: Date.now() + 13 * 3600_000 });
      push.forgetPushStatus();
      tab2.forgetPushStatus();
      const states = await Promise.all(tabs.map((tab) => tab.readPushState({ userId: user, config: CONFIG, win })));
      const record = readRecord();
      const subscription = state.subscription;
      assert.deepEqual(violations, [], `seed ${seed}: 다른 계정으로 등록했다`);
      assert.equal(states[0], states[1], `seed ${seed}: 두 탭의 상태가 다르다`);
      assert.ok(!record?.pending, `seed ${seed}: 등록 중 기록이 남았다`);
      if (!subscription) assert.equal(record, null, `seed ${seed}: 구독이 없는데 기록이 남았다`);
      if (states[0] === "on") {
        const row = server.rows.get(subscription.endpoint);
        assert.deepEqual(row, { user, version: record.version }, `seed ${seed}: 켜짐인데 서버와 다르다`);
      }
      if (record && subscription) {
        assert.equal(record.endpoint, subscription.endpoint, `seed ${seed}: 기록이 다른 구독을 가리킨다`);
        assert.deepEqual(server.rows.get(record.endpoint), { user: Number(record.user), version: record.version },
          `seed ${seed}: 마지막 동기화 뒤에도 기록과 서버가 다르다`);
      }
    } finally {
      if (!locks) delete globalThis.navigator.locks;
      tab2.setPushTimingForTest();
      t.mock.restoreAll();
    }
  }
});

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

const text = (parts) => parts.map((part) => (typeof part === "string" ? part : part.br ? " " : part.b || `[${part.icon}]`)).join("");

test("안드로이드는 설치 창을 띄울 수 있으면 버튼 하나, 못 띄우면 크롬 · 삼성 인터넷 3단계", () => {
  assert.deepEqual(installGuide({ tab: "android", platform: "android", browser: "chrome", canPrompt: true }),
    { kind: "android", button: "only", steps: [], note: "" });
  const chrome = installGuide({ tab: "android", platform: "android", browser: "chrome", canPrompt: false });
  assert.equal(chrome.button, null);
  assert.deepEqual(chrome.steps.map((s) => s.title), ["⋮ 누르기", "앱 설치", "설치"]);
  assert.equal(chrome.steps[0].hint, null);
  const samsung = installGuide({ tab: "android", platform: "android", browser: "samsung", canPrompt: false });
  assert.deepEqual(samsung.steps.map((s) => s.title), ["메뉴", "현재 페이지 추가", "홈 화면"]);
  const tablet = installGuide({ tab: "android", platform: "ios", browser: "safari", canPrompt: false, host: "gg-parrot.vercel.app" });
  assert.equal(text(tablet.steps[0].hint), "휴대폰 크롬에서 gg-parrot.vercel.app 을 먼저 열어요.", "다른 휴대폰 안내는 주소를 먼저 열게");
});

test("PC 머리글에도 '앱 설치' — QR 을 휴대폰으로 찍어 휴대폰에 설치한다(11/09 시안 A) · 앱 알림은 실행기가", () => {
  const pc = { navigator: { userAgent: UA.windowsEdge, platform: "Win32", maxTouchPoints: 0 }, matchMedia: () => ({ matches: false }) };
  assert.equal(pushSupport(pc), "desktop", "PC 알림은 데스크톱 실행기가 보낸다");
  assert.equal(offersAppInstall({ platform: "desktop", standalone: false }), true, "PC 화면을 보며 휴대폰으로 찍는다");
  assert.equal(offersAppInstall({ platform: "ios", standalone: false }), true, "아이패드(터치 되는 맥)도 휴대폰과 같이");
  assert.equal(offersAppInstall({ platform: "android", standalone: true }), false, "이미 앱으로 열었으면 없다");
  assert.equal(offersAppInstall({ platform: "desktop", standalone: true }), false);
  for (const tab of ["android", "ios"]) {
    const guide = installGuide({ tab, platform: "desktop", browser: "chrome", canPrompt: true });
    assert.equal(guide.button, null, "PC 에는 '바로 설치' 가 없다(PC 자체 설치 안내는 두지 않는다)");
    assert.equal(guide.steps.length, 3);
    assert.equal(guide.steps[0].title, "휴대폰으로 QR 찍기");
    assert.equal(guide.steps[0].qr, true, "01 단계에 QR 그림");
    assert.equal(text(guide.steps[0].text), "휴대폰 카메라로 찍으면 껄무새가 열려요.");
    assert.ok(guide.steps.slice(1).every((item) => !item.qr));
  }
  assert.deepEqual(installGuide({ tab: "android", platform: "desktop", browser: "chrome" }).steps.map((s) => s.title),
    ["휴대폰으로 QR 찍기", "⋮ 누르기", "앱 설치"]);
  const ios = installGuide({ tab: "ios", platform: "desktop", browser: "chrome" });
  assert.deepEqual(ios.steps.map((s) => s.title), ["휴대폰으로 QR 찍기", "⋯ 누르고 공유", "홈 화면에 추가"]);
  assert.equal(text(ios.steps[2].hint), "웹 앱으로 열기가 켜진 채로", "아이폰 메뉴 이름을 줄이지 않는다");
  assert.equal(ios.note, "push");
  const qr = readFileSync(new URL("../public/app-install-qr.svg", import.meta.url), "utf8");
  assert.match(qr, /https:\/\/gg-parrot\.vercel\.app\/\?install=1/, "QR 이 여는 주소를 파일에 적어 둔다");
  assert.match(qr, /<svg[^>]*viewBox="0 0 31 31"/);
});

test("QR 로 들어오면(?install=1) 설치 안내를 바로 열고 주소에서 표시를 지운다 — 앱으로 열었으면 열지 않는다", () => {
  const visit = (href) => {
    const calls = [];
    const win = { location: { href }, history: { state: { key: "k" }, replaceState: (...args) => calls.push(args) } };
    return { win, calls };
  };
  const phone = visit("https://gg-parrot.vercel.app/?install=1#top");
  assert.equal(openAppInstallFromLink(phone.win, () => true), true);
  assert.deepEqual(phone.calls, [[{ key: "k" }, "", "/#top"]], "다른 주소 · 상태는 그대로, install 만 지운다");
  closeAppInstall();
  const kept = visit("https://gg-parrot.vercel.app/agents?tab=1&install=1");
  openAppInstallFromLink(kept.win, () => true);
  assert.equal(kept.calls[0][2], "/agents?tab=1");
  closeAppInstall();
  const installed = visit("https://gg-parrot.vercel.app/?install=1");
  assert.equal(openAppInstallFromLink(installed.win, () => false), false, "이미 앱으로 열었으면 열지 않는다");
  assert.equal(installed.calls.length, 1, "그래도 표시는 지운다");
  const plain = visit("https://gg-parrot.vercel.app/?install=0");
  assert.equal(openAppInstallFromLink(plain.win, () => true), false);
  assert.equal(plain.calls.length, 0, "표시가 없으면 주소를 건드리지 않는다");
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
