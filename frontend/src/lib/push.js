// 앱(PWA) 알림 — 이 기기에서 '내 에이전트 코인 소식' 알림을 켜고 끈다. 보내는 쪽은 서버 push_alerts.
//
// 상태는 기기마다 다르다: 브라우저 구독(PushManager)이 있고, 그 구독을 켠 계정이 지금 계정이고, 서버도 그 기기를
// 그 계정으로 보내고 있으면 '켜짐'. 서버는 기기를 스스로 지울 수 있다(11번째 기기 · 끝난 구독 410 · 거절 반복).
// 켠 계정을 기억해 두는 까닭 — 로그아웃 정리가 실패한 기기에서 다른 사람이 로그인했을 때, 앞 사람이 켠 구독을
// 새 계정으로 옮겨 남의 기기에 알림이 가지 않게 한다(옮기지 않고 지운다).
import { api } from "../api.js";
import { captureAccountGuard } from "./auth.js";
import { currentDevice } from "./pwa.js";

const OWNER_KEY = "ggp_push_owner";
const SYNC_KEY = "ggp_push_synced_at";
const SYNC_EVERY_MS = 12 * 60 * 60 * 1000;
const STATUS_FRESH_MS = 15_000;

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

// 서버가 이 기기(엔드포인트)를 이 계정으로 보내고 있는지 — true | false | null(못 물음: 오프라인 등, 그때는 브라우저를 믿는다).
// 설정 · 채팅 단추 · 첫 묻기가 같은 순간 함께 읽으니 잠깐(STATUS_FRESH_MS) 같은 답을 나눠 쓴다.
let statusMemo = null;
function serverHas(userId, endpoint, { force = false, now = Date.now() } = {}) {
  const key = `${userId}|${endpoint}`;
  if (!force && statusMemo?.key === key && now - statusMemo.at < STATUS_FRESH_MS) return statusMemo.promise;
  const promise = api.pushStatus(endpoint).then((body) => Boolean(body?.subscribed), () => null);
  statusMemo = { key, at: now, promise };
  return promise;
}

// 시험 알림이 다 못 갔을 때처럼 서버가 기기를 지웠을 수 있으면 — 다음 읽기는 서버에 다시 묻는다.
export function forgetPushStatus() {
  statusMemo = null;
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
  let timer;
  const late = new Promise((_, reject) => { timer = setTimeout(() => reject(new Error("timeout")), ms); });
  return Promise.race([promise, late]).finally(() => clearTimeout(timer));
}

// 워커에 묻고 답을 기다린다(MessageChannel). 답이 없으면 null — 그런 말을 모르는 옛 워커.
function ask(worker, message, ms) {
  return new Promise((resolve) => {
    if (!worker?.postMessage || typeof MessageChannel === "undefined") {
      resolve(null);
      return;
    }
    const channel = new MessageChannel();
    const finish = (value) => {
      clearTimeout(timer);
      channel.port1.close();
      resolve(value);
    };
    const timer = setTimeout(() => finish(null), ms);
    channel.port1.onmessage = (event) => finish(event.data || {});
    try {
      worker.postMessage(message, [channel.port2]);
    } catch {
      finish(null);
    }
  });
}

function settled(worker, states, ms) {
  return new Promise((resolve) => {
    const check = () => {
      if (!states.includes(worker.state)) return;
      worker.removeEventListener("statechange", check);
      clearTimeout(timer);
      resolve();
    };
    const timer = setTimeout(() => {
      worker.removeEventListener("statechange", check);
      resolve();
    }, ms);
    worker.addEventListener("statechange", check);
    check();
  });
}

// 기다리는 새 워커를 올려 달라고 한다. 워커는 열린 창이 이 하나일 때만 받아들인다 — 다른 창이 옛 파일로 돌고 있으면
// 새 워커가 옛 캐시를 지워 그 창의 다음 화면이 깨지므로, 그때는 그 창들이 닫힐 때까지 둔다(build/serviceWorker.js).
async function activated(worker) {
  await settled(worker, ["installed", "activated", "redundant"], 8_000);
  if (worker.state !== "installed") return;
  const answer = await ask(worker, { type: "ggp:skip-waiting" }, 1_500);
  if (answer?.skipping) await settled(worker, ["activated", "redundant"], 8_000);
}

// 구독을 걸 워커 등록. 지금 워커가 푸시를 처리하면(ping 에 답함) 그대로 쓰고 업데이트를 건드리지 않는다.
// 푸시를 처리하지 못하는 옛 워커일 때만 새 워커를 찾아 올린다 — 옛 워커에 구독을 걸면 첫 알림을 놓친다.
async function pushRegistration(win) {
  const ready = await withTimeout(win.navigator.serviceWorker.ready, 10_000);
  if (await ask(ready.active, { type: "ggp:ping" }, 1_500)) return ready;
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
// 브라우저 구독 · 켠 계정이 맞아도 서버가 그 기기를 지웠으면 '꺼짐' — 켠 계정 기록도 지워 다음 동기화가 브라우저 구독을 정리한다.
export async function readPushState({ userId, config, win = globalThis.window } = {}) {
  const support = pushSupport(win);
  if (support !== "supported") return support;
  if (!config?.enabled) return "unconfigured";
  const permission = win.Notification.permission;
  if (permission === "denied") return "denied";
  const subscription = await (await registration(win))?.pushManager?.getSubscription().catch(() => null);
  if (!subscription || permission !== "granted") return "off";
  if (!userId || read(OWNER_KEY) !== String(userId)) return "off";
  const has = await serverHas(userId, subscription.endpoint);
  if (has === false) {
    if (read(OWNER_KEY) === String(userId)) {
      write(OWNER_KEY, "");
      write(SYNC_KEY, "");
    }
    return "off";
  }
  return "on";
}

let enabling = 0; // 켜는 중에는 동기화가 '남의 구독'으로 보고 새 구독을 끊지 않게

// 켜기. 클릭 처리기 안에서 '바로' 불러야 한다 — 권한 창은 사용자 동작 직후에만 뜬다(특히 iOS).
// 결과: on | denied | dismissed | unconfigured | unsupported | cancelled(켜는 동안 계정이 바뀜)
export function enablePush({ userId, config, win = globalThis.window } = {}) {
  if (pushSupport(win) !== "supported") return Promise.resolve("unsupported");
  // 시작한 계정이 끝까지 그대로인지. 켜는 동안 다른 탭에서 로그아웃하고 B 로 로그인하면, 지금 토큰(B)으로 서버에 등록하고
  // 기기에는 A 를 적어 B 가 켜지도 않은 알림을 받는다 — 바뀌면 그만두고, 그사이 만든 구독도 끊는다.
  const current = captureAccountGuard({ accountOnly: true });
  const Notice = win.Notification;
  const permission = Notice.permission === "granted" ? Promise.resolve("granted") : Notice.requestPermission();
  enabling += 1;
  return (async () => {
    const answer = await permission;
    if (answer !== "granted") return answer === "denied" ? "denied" : "dismissed";
    if (!config?.enabled || !config.public_key) return "unconfigured";
    if (!current()) return "cancelled";
    const ready = await pushRegistration(win);
    // 남아 있던 구독은 새로 받는다 — 서버가 지운 구독(끝남 410 · 거절 반복)을 다시 쓰면 또 지워져 켜지지 않는다.
    const stale = await ready.pushManager.getSubscription().catch(() => null);
    if (stale) await stale.unsubscribe().catch(() => {});
    if (!current()) return "cancelled";
    const subscription = await ready.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(config.public_key),
    });
    const drop = () => subscription.unsubscribe().catch(() => {});
    if (!current()) {
      await drop();
      return "cancelled";
    }
    const body = subscription.toJSON();
    try {
      // 바로 앞에서 계정을 확인했고 그 사이 기다림이 없다 — 요청은 시작한 계정의 토큰으로 나간다.
      await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
    } catch (error) {
      await drop();
      throw error;
    }
    if (!current()) {
      // 등록은 시작한 계정으로 됐지만 그 계정은 이 기기에서 나갔다 — 브라우저 구독을 끊는다(서버 줄은 다음 발송 때 410 으로 지워진다).
      await drop();
      return "cancelled";
    }
    write(OWNER_KEY, String(userId));
    write(SYNC_KEY, String(Date.now()));
    statusMemo = { key: `${userId}|${body.endpoint}`, at: Date.now(), promise: Promise.resolve(true) };
    return "on";
  })().finally(() => {
    enabling -= 1;
  });
}

// 끄기 — 서버에서 지우고 브라우저 구독도 끊는다. 로그아웃 뒤(local)에는 서버에 묻지 않는다:
// 끊긴 구독은 다음 발송 때 푸시 서비스가 410 으로 알려 서버가 지운다.
export async function disablePush({ local = false, win = globalThis.window } = {}) {
  write(OWNER_KEY, "");
  write(SYNC_KEY, "");
  statusMemo = null;
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

// 앱을 열 때 · 앱으로 돌아올 때 — 다른 계정이 켠 구독은 끊고, 켜 둔 기기는 가끔(12시간) 서버에 다시 맞춘다.
// 서버가 이미 지운 기기(11번째 기기로 밀림 · 끝난 구독)는 되살리지 않고 이 기기 구독도 끊는다 — 화면은 '꺼짐'이 되고,
// 켜기를 누르면 새 구독으로 다시 등록한다(다른 기기를 다시 밀어내는 핑퐁을 만들지 않는다).
export async function syncPush({ userId, win = globalThis.window, now = Date.now() } = {}) {
  if (!userId || pushSupport(win) !== "supported") return "skip";
  if (enabling) return "busy";
  const current = captureAccountGuard({ accountOnly: true });
  const subscription = await (await registration(win))?.pushManager?.getSubscription().catch(() => null);
  if (!current() || enabling) return "cancelled";
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
  const has = await serverHas(userId, subscription.endpoint, { force: true, now });
  if (!current() || enabling) return "cancelled";
  if (has === null) return "skip"; // 서버에 못 물었다 — 다음에
  if (!has) {
    await disablePush({ local: true, win });
    return "dropped";
  }
  const body = subscription.toJSON();
  await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
  write(SYNC_KEY, String(now));
  return "synced";
}

// 앱으로 처음 열었을 때 한 번 묻는 조건 — 로그인했고, 앱(홈 화면)으로 열었고, 이 기기에서 꺼져 있고, 권한을 아직 안 물었고,
// 이 기기에서 한 번도 답하지 않았고, 로그인 · 거래소 연결처럼 다른 일을 하는 화면이 아닐 때.
// 로그인 전에는 묻지 않는다 — '알림 받기'를 눌러도 켤 계정이 없어 답만 써 버리고 로그인 뒤에 다시 묻지 못한다.
const QUIET_PATHS = ["/login", "/forgot", "/reset", "/exchange-connect"];
export function shouldAskPush({ signedIn, standalone, state, permission, alreadyAsked, path }) {
  return Boolean(signedIn) && Boolean(standalone) && state === "off" && permission === "default" && !alreadyAsked
    && !QUIET_PATHS.includes(path);
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
