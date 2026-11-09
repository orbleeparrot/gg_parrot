// 앱(PWA) 알림 — 이 기기에서 '내 에이전트 코인 소식' 알림을 켜고 끈다. 보내는 쪽은 서버 push_alerts.
//
// 상태는 기기마다 다르다: 브라우저 구독(PushManager)이 있고, 그 구독을 켠 계정이 지금 계정이고, 서버도 그 기기를
// 그 계정으로 보내고 있으면 '켜짐'. 서버는 기기를 스스로 지울 수 있다(11번째 기기 · 끝난 구독 410 · 거절 반복).
// 켠 계정을 기억해 두는 까닭 — 로그아웃 정리가 실패한 기기에서 다른 사람이 로그인했을 때, 앞 사람이 켠 구독을
// 새 계정으로 옮겨 남의 기기에 알림이 가지 않게 한다(옮기지 않고 지운다).
//
// 구독을 바꾸는 일(켜기 · 끄기 · 동기화)은 하나씩만 돈다(exclusive) — 탭 사이에도(Web Locks, 없으면 한 탭 안에서만).
// 기다리던 옛 작업이 나중 작업이 만든 구독을 끊지 않게. 계정에 묶인 요청은 마지막 기다림 바로 뒤에 계정을 다시 확인하고,
// 켠 계정과 함께 그 구독의 엔드포인트도 적어 두어 실패를 되돌릴 때 자기 구독의 기록만 지운다.
import { api } from "../api.js";
import { captureAccountGuard } from "./auth.js";
import { currentDevice } from "./pwa.js";

const OWNER_KEY = "ggp_push_owner";
const OWNER_ENDPOINT_KEY = "ggp_push_owner_endpoint";
const SYNC_KEY = "ggp_push_synced_at";
export const PUSH_STORAGE_PREFIX = "ggp_push_"; // 다른 탭이 켜고 끈 것을 알아채는 storage 이벤트 거름(usePushState)
const SYNC_EVERY_MS = 12 * 60 * 60 * 1000;
const STATUS_FRESH_MS = 15_000;
const timing = { ping: 1_500, finalPing: 3_000 };

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

// 이 계정이 켠 바로 그 구독인지 — 계정만 맞고 엔드포인트가 다르면(다른 탭이 새로 켬 · 브라우저가 바꿈) 내 기록이 아니다.
function owns(userId, endpoint) {
  return Boolean(userId) && read(OWNER_KEY) === String(userId) && read(OWNER_ENDPOINT_KEY) === endpoint;
}

function setOwner(userId, endpoint, now = Date.now()) {
  write(OWNER_KEY, String(userId));
  write(OWNER_ENDPOINT_KEY, endpoint);
  write(SYNC_KEY, String(now));
}

// 기록을 지운다. endpoint 를 주면 그 구독의 기록일 때만 — 다른 탭이 그사이 켠 새 구독의 기록은 남긴다.
function clearOwner(endpoint = "") {
  if (endpoint && read(OWNER_ENDPOINT_KEY) !== endpoint) return;
  write(OWNER_KEY, "");
  write(OWNER_ENDPOINT_KEY, "");
  write(SYNC_KEY, "");
}

export function urlBase64ToUint8Array(text) {
  const base = String(text || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = base + "=".repeat((4 - (base.length % 4)) % 4);
  const raw = globalThis.atob(padded);
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

// 서버 설정(켜졌는지 · 공개키). 못 읽었으면 failed 를 붙여 돌려준다 — 서버가 꺼 둔 것(enabled:false)과 달리
// 화면이 앱으로 돌아올 때 · 연결이 돌아올 때 다시 읽는다(usePushState).
let configPromise = null;
export function loadPushConfig({ refresh = false } = {}) {
  if (!configPromise || refresh) {
    configPromise = api.pushConfig().catch(() => {
      configPromise = null; // 일시 실패면 다음에 다시 묻는다
      return { enabled: false, public_key: "", failed: true };
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

// 시험용 — 워커 응답 기다림을 줄인다.
export function setPushTimingForTest(values = {}) {
  Object.assign(timing, { ping: 1_500, finalPing: 3_000 }, values);
}

// 구독을 바꾸는 일을 한 줄로 세운다 — Web Locks 가 있으면 같은 사이트의 모든 탭 · 창이 한 줄(아이폰 16.4+ · 안드로이드
// 크롬 모두 있음), 없으면 이 탭 안에서만. 안에서 다시 exclusive 를 부르지 않는다(내부 도우미는 dropLocal 등을 쓴다).
// 일 하나가 끝나지 않으면 줄 전체가 멈추므로, 안에서 기다리는 것은 모두 시간 제한이 있다.
const LOCK_NAME = "ggp-push-subscription";
let chain = Promise.resolve();
function exclusive(task) {
  const locks = globalThis.navigator?.locks;
  if (typeof locks?.request === "function") return locks.request(LOCK_NAME, () => task());
  const run = chain.then(task, task);
  chain = run.catch(() => {});
  return run;
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

async function currentSubscription(win) {
  return (await (await registration(win))?.pushManager?.getSubscription().catch(() => null)) || null;
}

// 내가 본 그 구독일 때만 끊는다 — 그사이 다른 작업(다른 탭 · 다른 계정)이 새로 만든 구독은 건드리지 않는다.
async function dropOwn(win, endpoint) {
  const now = await currentSubscription(win);
  if (now && now.endpoint === endpoint) await now.unsubscribe().catch(() => {});
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

async function handlesPush(worker, ms) {
  return Boolean((await ask(worker, { type: "ggp:ping" }, ms))?.push);
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

// 구독을 걸 워커 등록 — 활성 워커가 푸시를 처리할 때만 돌려준다(아니면 null). 지금 워커가 ping 에 답하면 업데이트를
// 건드리지 않는다. 옛 워커(푸시 처리 없음)면 새 워커를 찾아 올려 보고, 그래도 활성 워커가 답하지 않으면(다른 창 때문에
// 새 워커가 기다리는 중) 켜지 않는다 — 옛 워커에 구독을 걸면 알림이 와도 띄우지 못한다.
async function pushRegistration(win) {
  const ready = await withTimeout(win.navigator.serviceWorker.ready, 10_000);
  if (await handlesPush(ready.active, timing.ping)) return ready;
  try {
    await ready.update();
  } catch {
    /* 오프라인이면 지금 워커로 */
  }
  const pending = ready.installing || ready.waiting;
  if (pending) await activated(pending);
  return (await handlesPush(ready.active, timing.finalPing)) ? ready : null;
}

// 이 기기의 상태: on | off | denied | unconfigured | desktop | needs-install | in-app | unsupported
// 읽기만 한다 — 브라우저 구독 · 켠 계정이 맞아도 서버가 그 기기를 지웠으면 '꺼짐'. 저장소는 건드리지 않는다
// (늦게 온 옛 구독의 답이 그사이 켠 새 구독의 기록을 지우지 않게). 브라우저 구독 정리는 syncPush 가 한다.
export async function readPushState({ userId, config, win = globalThis.window } = {}) {
  const support = pushSupport(win);
  if (support !== "supported") return support;
  if (!config?.enabled) return "unconfigured";
  const permission = win.Notification.permission;
  if (permission === "denied") return "denied";
  const subscription = await currentSubscription(win);
  if (!subscription || permission !== "granted") return "off";
  if (!owns(userId, subscription.endpoint)) return "off";
  return (await serverHas(userId, subscription.endpoint)) === false ? "off" : "on";
}

// 켜기. 클릭 처리기 안에서 '바로' 불러야 한다 — 권한 창은 사용자 동작 직후에만 뜬다(특히 iOS).
// 결과: on | denied | dismissed | unconfigured | unsupported | cancelled(켜는 동안 계정이 바뀜)
// | update-pending(옛 워커가 남아 있고 다른 창 때문에 새 워커로 못 바꿈)
export function enablePush({ userId, config, win = globalThis.window } = {}) {
  if (pushSupport(win) !== "supported") return Promise.resolve("unsupported");
  // 시작한 계정이 끝까지 그대로인지. 켜는 동안 다른 탭에서 로그아웃하고 B 로 로그인하면, 지금 토큰(B)으로 서버에 등록하고
  // 기기에는 A 를 적어 B 가 켜지도 않은 알림을 받는다 — 바뀌면 그만두고, 그사이 내가 만든 구독만 끊는다.
  const current = captureAccountGuard({ accountOnly: true });
  const Notice = win.Notification;
  const permission = Notice.permission === "granted" ? Promise.resolve("granted") : Promise.resolve(Notice.requestPermission());
  return permission.then((answer) => {
    if (answer !== "granted") return answer === "denied" ? "denied" : "dismissed";
    if (!config?.enabled || !config.public_key) return "unconfigured";
    return exclusive(async () => {
      if (!current()) return "cancelled";
      const ready = await pushRegistration(win);
      if (!current()) return "cancelled"; // 기다리는 동안 계정이 바뀌었다 — 아무 구독도 건드리지 않는다
      if (!ready) return "update-pending";
      // 남아 있던 구독은 새로 받는다 — 서버가 지운 구독(끝남 410 · 거절 반복)을 다시 쓰면 또 지워져 켜지지 않는다.
      const stale = await ready.pushManager.getSubscription().catch(() => null);
      if (!current()) return "cancelled";
      if (stale) await stale.unsubscribe().catch(() => {});
      const subscription = await withTimeout(ready.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(config.public_key),
      }), 20_000);
      const endpoint = subscription.endpoint;
      // 되돌리기는 이 구독 것만 — 다른 탭이 그사이 켠 새 구독과 그 기록은 남긴다.
      const undo = async () => {
        await dropOwn(win, endpoint);
        clearOwner(endpoint);
      };
      if (!current()) {
        await undo();
        return "cancelled";
      }
      // 서버 등록 전에 먼저 적는다 — 다른 탭의 동기화가 '켠 계정 없는 구독'으로 보고 끊지 않게(실패하면 되돌린다).
      setOwner(userId, endpoint);
      const body = subscription.toJSON();
      try {
        // 바로 앞에서 계정을 확인했고 그 사이 기다림이 없다 — 요청은 시작한 계정의 토큰으로 나간다.
        await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
      } catch (error) {
        await undo();
        throw error;
      }
      if (!current()) {
        // 등록은 시작한 계정으로 됐지만 그 계정은 이 기기에서 나갔다 — 내 구독을 끊는다(서버 줄은 다음 발송 때 410 으로 지워진다).
        await undo();
        return "cancelled";
      }
      statusMemo = { key: `${userId}|${endpoint}`, at: Date.now(), promise: Promise.resolve(true) };
      return "on";
    });
  });
}

// 이 기기 구독을 끊는다. 서버에 알리는 것은 local=false 이고, 시작한 계정이 그대로일 때만 — 계정이 바뀌었으면
// 지금 토큰(다른 계정)으로 해제를 보내지 않는다(끊긴 구독은 다음 발송 때 410 으로 지워진다).
async function dropLocal({ local, win, current = () => true }) {
  clearOwner();
  statusMemo = null;
  if (pushSupport(win) !== "supported") return "off";
  const subscription = await currentSubscription(win);
  if (!subscription) return "off";
  if (!local && current()) {
    try {
      await api.pushUnsubscribe(subscription.endpoint);
    } catch {
      /* 서버가 못 받아도 브라우저 구독은 끊는다 */
    }
  }
  await dropOwn(win, subscription.endpoint);
  return "off";
}

// 끄기 — 서버에서 지우고 브라우저 구독도 끊는다. 로그아웃 뒤(local)에는 서버에 묻지 않는다:
// 끊긴 구독은 다음 발송 때 푸시 서비스가 410 으로 알려 서버가 지운다.
export function disablePush({ local = false, win = globalThis.window } = {}) {
  const current = captureAccountGuard({ accountOnly: true });
  return exclusive(() => dropLocal({ local, win, current }));
}

// 앱을 열 때 · 앱으로 돌아올 때 — 다른 계정이 켠 구독은 끊고, 켜 둔 기기는 가끔(12시간) 서버에 다시 맞춘다.
// 서버가 이미 지운 기기(11번째 기기로 밀림 · 끝난 구독)는 되살리지 않고 이 기기 구독도 끊는다 — 화면은 '꺼짐'이 되고,
// 켜기를 누르면 새 구독으로 다시 등록한다(다른 기기를 다시 밀어내는 핑퐁을 만들지 않는다).
export function syncPush({ userId, win = globalThis.window, now = Date.now() } = {}) {
  if (!userId || pushSupport(win) !== "supported") return Promise.resolve("skip");
  const current = captureAccountGuard({ accountOnly: true });
  return exclusive(async () => {
    if (!current()) return "cancelled";
    const subscription = await currentSubscription(win);
    if (!current()) return "cancelled";
    if (!subscription) {
      if (read(OWNER_KEY)) clearOwner();
      return "none";
    }
    if (!owns(userId, subscription.endpoint)) {
      await dropLocal({ local: true, win });
      return "dropped";
    }
    if (win.Notification.permission !== "granted") return "skip";
    if (now - Number(read(SYNC_KEY) || 0) < SYNC_EVERY_MS) return "fresh";
    const has = await serverHas(userId, subscription.endpoint, { force: true, now });
    if (has === null) return "skip"; // 서버에 못 물었다 — 다음에
    // 물어보는 사이 다른 탭이 구독을 바꿨으면 이 답은 옛 구독 것이다 — 다음에 다시 맞춘다.
    const latest = await currentSubscription(win);
    // 마지막 기다림 바로 뒤 — 여기서부터 서버에 쓰기까지 기다림이 없다. 그사이 A→B 로 바뀌었으면 B 의 토큰으로
    // A 의 구독을 등록하게 되므로, 계정에 묶인 요청 바로 앞에서 다시 확인한다.
    if (!current()) return "cancelled";
    if (latest?.endpoint !== subscription.endpoint || !owns(userId, subscription.endpoint)) return "changed";
    if (!has) {
      await dropLocal({ local: true, win });
      return "dropped";
    }
    const body = subscription.toJSON();
    await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
    if (!current() || !owns(userId, subscription.endpoint)) return "cancelled"; // 등록하는 사이 바뀌었으면 기록하지 않는다
    write(SYNC_KEY, String(now));
    return "synced";
  });
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
