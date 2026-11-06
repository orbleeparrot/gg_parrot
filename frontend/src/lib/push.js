// 앱(PWA) 알림 — 이 기기에서 '내 에이전트 코인 소식' 알림을 켜고 끈다. 보내는 쪽은 서버 push_alerts.
//
// 상태는 기기마다 다르다: 브라우저 구독(PushManager)이 있고, 그 구독을 켠 계정이 지금 계정이면 '켜짐'.
// 켠 계정을 기억해 두는 까닭 — 로그아웃 정리가 실패한 기기에서 다른 사람이 로그인했을 때, 앞 사람이 켠 구독을
// 새 계정으로 옮겨 남의 기기에 알림이 가지 않게 한다(옮기지 않고 지운다).
import { api } from "../api.js";
import { currentDevice } from "./pwa.js";

const OWNER_KEY = "ggp_push_owner";
const SYNC_KEY = "ggp_push_synced_at";
const SYNC_EVERY_MS = 12 * 60 * 60 * 1000;

function storage() {
  try {
    return globalThis.localStorage || null;
  } catch {
    return null;
  }
}

function read(key) {
  try {
    return storage()?.getItem(key) || "";
  } catch {
    return "";
  }
}

function write(key, value) {
  try {
    if (value) storage()?.setItem(key, value);
    else storage()?.removeItem(key);
  } catch {
    /* 저장이 막힌 브라우저 — 다음에 다시 맞춘다 */
  }
}

export function urlBase64ToUint8Array(text) {
  const base = String(text || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = base + "=".repeat((4 - (base.length % 4)) % 4);
  const raw = globalThis.atob(padded);
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

function sameKey(buffer, publicKey) {
  if (!buffer) return true; // 옛 브라우저는 구독 키를 보여 주지 않는다 — 같다고 본다
  const left = new Uint8Array(buffer);
  const right = urlBase64ToUint8Array(publicKey);
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

let configPromise = null;
export function loadPushConfig({ refresh = false } = {}) {
  if (!configPromise || refresh) {
    configPromise = api.pushConfig().catch(() => {
      configPromise = null; // 일시 실패면 다음에 다시 묻는다
      return { enabled: false, public_key: "" };
    });
  }
  return configPromise;
}

// supported | desktop(PC 알림은 데스크톱 실행기가 보낸다 — 웹 푸시까지 켜면 같은 체결이 두 번 울린다)
// | needs-install(아이폰 사파리 탭 — 홈 화면 앱에서만 된다) | in-app | unsupported
export function pushSupport(win = globalThis.window, device = currentDevice(win)) {
  if (device.platform === "desktop") return "desktop";
  if (device.browser === "in-app") return "in-app";
  const ready = Boolean(win?.navigator && "serviceWorker" in win.navigator && "PushManager" in win && "Notification" in win);
  if (ready) return "supported";
  return device.platform === "ios" && !device.standalone ? "needs-install" : "unsupported";
}

async function registration(win = globalThis.window) {
  try {
    return (await win.navigator.serviceWorker.getRegistration("/")) || null;
  } catch {
    return null;
  }
}

function withTimeout(promise, ms) {
  return Promise.race([promise, new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), ms))]);
}

// 새 워커가 기다리는 중이면 바로 올린다 — 옛 워커(푸시 처리 없음)에 구독을 걸면 첫 알림을 놓친다.
function activated(worker) {
  return new Promise((resolve) => {
    const settle = () => {
      if (worker.state === "activated" || worker.state === "redundant") {
        worker.removeEventListener("statechange", settle);
        resolve();
      } else if (worker.state === "installed") {
        worker.postMessage({ type: "ggp:skip-waiting" });
      }
    };
    worker.addEventListener("statechange", settle);
    settle();
    setTimeout(resolve, 8_000);
  });
}

async function freshRegistration(win) {
  const ready = await withTimeout(win.navigator.serviceWorker.ready, 10_000);
  try {
    await ready.update();
  } catch {
    /* 오프라인이면 지금 워커로 */
  }
  const pending = ready.installing || ready.waiting;
  if (pending) await activated(pending);
  return ready;
}

// 이 기기의 상태: on | off | denied | unconfigured | desktop | needs-install | in-app | unsupported
export async function readPushState({ userId, config, win = globalThis.window } = {}) {
  const support = pushSupport(win);
  if (support !== "supported") return support;
  if (!config?.enabled) return "unconfigured";
  const permission = win.Notification.permission;
  if (permission === "denied") return "denied";
  const subscription = await (await registration(win))?.pushManager?.getSubscription().catch(() => null);
  if (!subscription || permission !== "granted") return "off";
  return read(OWNER_KEY) === String(userId) ? "on" : "off";
}

// 켜기. 클릭 처리기 안에서 '바로' 불러야 한다 — 권한 창은 사용자 동작 직후에만 뜬다(특히 iOS).
// 결과: on | denied | dismissed | unconfigured | unsupported
export function enablePush({ userId, config, win = globalThis.window } = {}) {
  if (pushSupport(win) !== "supported") return Promise.resolve("unsupported");
  const Notice = win.Notification;
  const permission = Notice.permission === "granted" ? Promise.resolve("granted") : Notice.requestPermission();
  return (async () => {
    const answer = await permission;
    if (answer !== "granted") return answer === "denied" ? "denied" : "dismissed";
    if (!config?.enabled || !config.public_key) return "unconfigured";
    const ready = await freshRegistration(win);
    let subscription = await ready.pushManager.getSubscription();
    if (subscription && !sameKey(subscription.options?.applicationServerKey, config.public_key)) {
      await subscription.unsubscribe().catch(() => {});
      subscription = null;
    }
    if (!subscription) {
      subscription = await ready.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(config.public_key),
      });
    }
    const body = subscription.toJSON();
    await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
    write(OWNER_KEY, String(userId));
    write(SYNC_KEY, String(Date.now()));
    return "on";
  })();
}

// 끄기 — 서버에서 지우고 브라우저 구독도 끊는다. 로그아웃 뒤(local)에는 서버에 묻지 않는다:
// 끊긴 구독은 다음 발송 때 푸시 서비스가 410 으로 알려 서버가 지운다.
export async function disablePush({ local = false, win = globalThis.window } = {}) {
  write(OWNER_KEY, "");
  write(SYNC_KEY, "");
  if (pushSupport(win) !== "supported") return "off";
  const subscription = await (await registration(win))?.pushManager?.getSubscription().catch(() => null);
  if (!subscription) return "off";
  if (!local) {
    try {
      await api.pushUnsubscribe(subscription.endpoint);
    } catch {
      /* 서버가 못 받아도 브라우저 구독은 끊는다 */
    }
  }
  await subscription.unsubscribe().catch(() => {});
  return "off";
}

// 앱을 열 때 — 켜 둔 기기는 서버 등록을 가끔(12시간) 다시 맞추고, 다른 계정이 켠 구독은 끊는다.
export async function syncPush({ userId, win = globalThis.window, now = Date.now() } = {}) {
  if (!userId || pushSupport(win) !== "supported") return "skip";
  const subscription = await (await registration(win))?.pushManager?.getSubscription().catch(() => null);
  if (!subscription) {
    if (read(OWNER_KEY)) write(OWNER_KEY, "");
    return "none";
  }
  const owner = read(OWNER_KEY);
  if (owner !== String(userId)) {
    await disablePush({ local: true, win });
    return "dropped";
  }
  if (win.Notification.permission !== "granted") return "skip";
  if (now - Number(read(SYNC_KEY) || 0) < SYNC_EVERY_MS) return "fresh";
  const body = subscription.toJSON();
  await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
  write(SYNC_KEY, String(now));
  return "synced";
}

// 앱으로 처음 열었을 때 한 번 묻는 조건 — 앱(홈 화면 · 바탕화면)으로 열었고, 이 기기에서 꺼져 있고, 권한을 아직 안 물었고,
// 이 기기에서 한 번도 답하지 않았고, 로그인 · 거래소 연결처럼 다른 일을 하는 화면이 아닐 때.
const QUIET_PATHS = ["/login", "/forgot", "/reset", "/exchange-connect"];
export function shouldAskPush({ standalone, state, permission, alreadyAsked, path }) {
  return Boolean(standalone) && state === "off" && permission === "default" && !alreadyAsked && !QUIET_PATHS.includes(path);
}

export function sendTestPush() {
  return api.pushTest();
}

// 알림을 눌렀을 때 서비스 워커가 열린 화면에 보내는 이동 요청 — 같은 사이트 경로만 받는다.
export function openTarget(message, origin = globalThis.location?.origin) {
  if (!message || message.type !== "ggp:open") return "";
  try {
    const url = new URL(String(message.url || "/"), origin);
    return url.origin === origin ? url.pathname + url.search + url.hash : "";
  } catch {
    return "";
  }
}
