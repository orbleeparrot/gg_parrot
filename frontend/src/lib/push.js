// 앱(PWA) 알림 — 이 기기에서 '내 에이전트 코인 소식' 알림을 켜고 끄는 일(브라우저 · 서버 · 기기 기록). 보내는 쪽은 서버 push_alerts.
// 화면은 이 파일을 직접 부르지 않고 pushController(상태 하나)를 구독한다.
//
// '켜짐'은 세 가지가 맞을 때만: 브라우저 구독이 있고, 이 기기 기록(ggp_push_device)이 지금 계정 · 그 구독 주소이고,
// 서버가 그 구독을 이 계정으로 확인한다. 서버는 구독마다 세대(version)를 두어 계정이 바뀌면 올리고, 앱의 재등록 · 해제는
// 기대한 세대를 함께 보내 서버가 조건부로만 바꾼다(늦게 간 옛 요청이 새 상태를 덮지 않게).
//
// 구독을 바꾸는 일(켜기 · 끄기 · 동기화)은 하나씩만 돈다(exclusive) — 탭 사이에도(Web Locks, 없으면 한 탭 안에서만).
// 계정에 묶인 요청은 마지막 기다림 바로 뒤에 계정을 다시 확인한다.
import { api } from "../api.js";
import { captureAccountGuard } from "./auth.js";
import { currentDevice } from "./pwa.js";

export const RECORD_KEY = "ggp_push_device"; // { v: 2, user, endpoint, version, syncedAt, pending?, op?, startedAt? }
const LEGACY_KEYS = ["ggp_push_owner", "ggp_push_owner_endpoint", "ggp_push_synced_at"]; // v1 (11/06~11/09 개발판)
const SYNC_EVERY_MS = 12 * 60 * 60 * 1000;
// 등록 중 기록이 진행 중인 켜기 없이 남은 것으로 보는 시간 — Web Locks 가 없는 브라우저에서만(있으면 잠금으로 바로 안다).
// 켜기 안의 기다림(구독 20초 · 서버 등록 10초)보다 길게.
const PENDING_STALE_MS = 60_000;
const inflight = new Set(); // 이 탭에서 진행 중인 켜기(등록 중 기록의 op)
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

// --- 이 기기 기록 -------------------------------------------------------------------
// 형식에 버전(v)을 둔다 — 형식이 바뀌면 '새 키가 없다'로 판단해 정상 구독을 끊지 않고 migrateLegacy 가 옮긴다.
export function readRecord() {
  try {
    const value = JSON.parse(read(RECORD_KEY) || "null");
    return value && value.v === 2 && value.user && value.endpoint ? value : null;
  } catch {
    return null;
  }
}

function writeRecord(record) {
  write(RECORD_KEY, JSON.stringify({ v: 2, ...record }));
}

// 기록을 지운다. endpoint 를 주면 그 구독의 기록일 때만 — 다른 탭이 그사이 켠 새 구독의 기록은 남긴다.
function clearRecord(endpoint = "") {
  const record = readRecord();
  if (endpoint && record && record.endpoint !== endpoint) return;
  write(RECORD_KEY, "");
}

// 이 계정이 켜고 서버가 확인한 바로 그 구독인지(등록 중인 기록은 아직 아니다).
function owns(record, userId, endpoint) {
  return Boolean(record && userId && !record.pending && record.user === String(userId) && record.endpoint === endpoint);
}

function hasLegacy() {
  return LEGACY_KEYS.some((key) => read(key));
}

export function urlBase64ToUint8Array(text) {
  const base = String(text || "").replace(/-/g, "+").replace(/_/g, "/");
  const padded = base + "=".repeat((4 - (base.length % 4)) % 4);
  const raw = globalThis.atob(padded);
  return Uint8Array.from(raw, (char) => char.charCodeAt(0));
}

// 서버 설정(켜졌는지 · 공개키). 못 읽었으면 failed 를 붙인다 — 서버가 꺼 둔 것(enabled:false)과 구분해 다시 읽는다.
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

// 서버가 이 기기(엔드포인트)를 이 계정으로 보내고 있는지와 그 세대 — { subscribed, version } | null(못 물음).
// 같은 순간 여러 곳이 읽으면 잠깐(STATUS_FRESH_MS) 같은 답을 나눠 쓴다. 못 물은 답(null)은 나눠 쓰지 않는다 —
// 연결이 돌아온 직후의 확인이 옛 실패를 다시 쓰지 않고 바로 서버에 묻게. cachedOnly 면 새로 묻지 않는다(나눠 쓸 답이
// 없으면 null) — 방금 서버에 못 물은 맞추기 뒤에 상태를 다시 읽을 때 같은 실패를 한 번 더 묻지 않게.
let statusMemo = null;
function serverStatus(userId, endpoint, { force = false, cachedOnly = false, now = Date.now() } = {}) {
  const key = `${userId}|${endpoint}`;
  if (!force && statusMemo?.key === key && now - statusMemo.at < STATUS_FRESH_MS) return statusMemo.promise;
  if (cachedOnly) return Promise.resolve(null);
  const memo = { key, at: now, promise: null };
  memo.promise = api.pushStatus(endpoint).then(
    (body) => ({ subscribed: Boolean(body?.subscribed), version: Number(body?.version) || 0 }),
    () => {
      if (statusMemo === memo) statusMemo = null;
      return null;
    });
  statusMemo = memo;
  return memo.promise;
}

export function forgetPushStatus() {
  statusMemo = null;
}

export function setPushTimingForTest(values = {}) {
  Object.assign(timing, { ping: 1_500, finalPing: 3_000 }, values);
}

// 구독을 바꾸는 일을 한 줄로 — Web Locks 가 있으면 같은 사이트의 모든 탭 · 창이 한 줄(아이폰 16.4+ · 안드로이드 크롬),
// 없으면 이 탭 안에서만. 안에서 다시 exclusive 를 부르지 않는다. 안에서 기다리는 것은 모두 시간 제한이 있다.
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
// 건드리지 않는다. 옛 워커(푸시 처리 없음)면 새 워커를 찾아 올려 보고, 그래도 활성 워커가 답하지 않으면 켜지 않는다.
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

// 이 기기의 상태(읽기만 한다 — 저장소를 바꾸지 않는다):
// loading | on | off | denied | unconfigured | unknown(확인 못 함 — '꺼짐'과 다르다) | desktop | needs-install | in-app | unsupported
// askServer=false — 서버에 새로 묻지 않는다(나눠 쓸 답이 없으면 'unknown'). 방금 서버에 못 물은 맞추기 뒤에 쓴다.
export async function readPushState({ userId, config, win = globalThis.window, askServer = true } = {}) {
  const support = pushSupport(win);
  if (support !== "supported") return support;
  if (!config) return "loading";
  if (config.failed) return "unknown";
  if (!config.enabled) return "unconfigured";
  const permission = win.Notification.permission;
  if (permission === "denied") return "denied";
  const subscription = await currentSubscription(win);
  if (!subscription || permission !== "granted") return "off";
  const record = readRecord();
  // 서버 확인이 남은 기록 — '꺼짐'이 아니라 '확인 못 함'. 맞추기(syncPush)가 서버에 물어 확정하면 바뀐다.
  // · 이전 형식 기록(아직 못 옮김) · 진행 중인 켜기 없이 남은 '등록 중' 기록(이 탭에서 켜는 중이면 controller 가 busy 로 보여 준다)
  if (!record && hasLegacy()) return "unknown";
  if (record?.pending && record.user === String(userId) && record.endpoint === subscription.endpoint) {
    return inflight.has(record.op) ? "off" : "unknown";
  }
  if (!owns(record, userId, subscription.endpoint)) return "off";
  const server = await serverStatus(userId, subscription.endpoint, { cachedOnly: !askServer });
  if (server === null) return "unknown";
  return server.subscribed ? "on" : "off";
}

// 켜기. 클릭 처리기 안에서 '바로' 불러야 한다 — 권한 창은 사용자 동작 직후에만 뜬다(특히 iOS).
// 결과: on | denied | dismissed | unconfigured | unsupported | cancelled(켜는 동안 계정이 바뀜)
// | update-pending(옛 워커가 남아 있고 다른 창 때문에 새 워커로 못 바꿈)
// '켜짐'은 서버가 받은 뒤에만 기록한다 — 등록 중에는 pending 기록(다른 탭의 동기화가 남의 구독으로 보고 끊지 않게).
export function enablePush({ userId, config, win = globalThis.window } = {}) {
  if (pushSupport(win) !== "supported") return Promise.resolve("unsupported");
  const current = captureAccountGuard({ accountOnly: true });
  const Notice = win.Notification;
  const permission = Notice.permission === "granted" ? Promise.resolve("granted") : Promise.resolve(Notice.requestPermission());
  return permission.then((answer) => {
    if (answer !== "granted") return answer === "denied" ? "denied" : "dismissed";
    if (!config?.enabled || !config.public_key) return "unconfigured";
    const onDone = [];
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
      const undo = async () => {
        await dropOwn(win, endpoint);
        clearRecord(endpoint);
      };
      if (!current()) {
        await undo();
        return "cancelled";
      }
      const op = Math.random().toString(36).slice(2);
      inflight.add(op);
      onDone.push(() => inflight.delete(op));
      writeRecord({ user: String(userId), endpoint, version: 0, syncedAt: 0, pending: true, op, startedAt: Date.now() });
      const body = subscription.toJSON();
      let saved;
      try {
        // 바로 앞에서 계정을 확인했고 그 사이 기다림이 없다 — 요청은 시작한 계정의 토큰으로 나간다.
        saved = await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform });
      } catch (error) {
        await undo();
        throw error;
      }
      if (!current()) {
        // 등록은 시작한 계정으로 됐지만 그 계정은 이 기기에서 나갔다 — 내 구독을 끊는다(서버 줄은 다음 발송 때 410 으로 지워진다).
        await undo();
        return "cancelled";
      }
      const version = Number(saved?.version) || 0;
      writeRecord({ user: String(userId), endpoint, version, syncedAt: Date.now() });
      statusMemo = { key: `${userId}|${endpoint}`, at: Date.now(), promise: Promise.resolve({ subscribed: true, version }) };
      return "on";
    }).finally(() => onDone.forEach((done) => done()));
  });
}

// 이 기기 구독을 끊는다. 서버에 알리는 것은 local=false 이고 시작한 계정이 그대로일 때만 — 기록에 있는 세대를 함께 보내
// 서버가 그 세대만 지운다(그사이 다른 계정이 이 기기로 켰으면 그대로 둔다).
async function dropLocal({ local, win, current = () => true }) {
  const record = readRecord();
  clearRecord();
  statusMemo = null;
  if (pushSupport(win) !== "supported") return "off";
  const subscription = await currentSubscription(win);
  if (!subscription) return "off";
  if (!local && current()) {
    const expected = record && record.endpoint === subscription.endpoint && !record.pending ? record.version : undefined;
    try {
      await api.pushUnsubscribe(subscription.endpoint, expected);
    } catch {
      /* 서버가 못 받아도 브라우저 구독은 끊는다(다음 발송 때 410 으로 지워진다) */
    }
  }
  await dropOwn(win, subscription.endpoint);
  return "off";
}

// 끄기 — 서버에서 지우고 브라우저 구독도 끊는다. 로그아웃 뒤(local)에는 서버에 묻지 않는다.
export function disablePush({ local = false, win = globalThis.window } = {}) {
  const current = captureAccountGuard({ accountOnly: true });
  return exclusive(() => dropLocal({ local, win, current }));
}

// 이전 형식(v1: 켠 계정 · 주소 · 동기화 시각을 키 셋에) 기록을 옮긴다 — 지금 구독이 그 기록의 구독이고 서버가 이 계정으로
// 확인해 주면 세대를 받아 새 기록으로(동의를 지킨다). 서버에 못 물었으면 옛 기록을 그대로 두고 다음에.
async function migrateLegacy({ userId, win, current }) {
  if (readRecord() || !hasLegacy()) return "none";
  const [owner, ownerEndpoint, syncedAt] = LEGACY_KEYS.map(read);
  const subscription = await currentSubscription(win);
  const forget = () => LEGACY_KEYS.forEach((key) => write(key, ""));
  if (!subscription || owner !== String(userId) || (ownerEndpoint && ownerEndpoint !== subscription.endpoint)) {
    forget();
    return "dropped";
  }
  const server = await serverStatus(userId, subscription.endpoint, { force: true });
  if (!current() || readRecord()) return "none"; // 그사이 계정이 바뀌었거나 새로 켰다
  if (server === null) return "later";
  if (server.subscribed) {
    writeRecord({ user: String(userId), endpoint: subscription.endpoint, version: server.version,
      syncedAt: Number(syncedAt) || 0 });
  }
  forget();
  return server.subscribed ? "migrated" : "dropped";
}

// 맞추기(reconcile) — 시작 · 로그인 · 연결 복구 · 앱으로 돌아옴 · 다른 창의 변경 때 pushController 가 부른다.
// 브라우저 구독 · 권한 · 로그인 계정을 보고, 서버에 소유권과 세대를 물어 기기 기록을 확정한다.
// - 이전 형식 기록은 서버가 확인해 주면 옮긴다(동의 보존). 못 물었으면 다음 기회에.
// - 진행 중인 켜기 없이 남은 '등록 중' 기록(켜는 도중 앱이 닫힘)은 서버가 가진 그대로 확정하거나 정리한다.
// - 다른 계정이 켠 구독 · 서버가 지운 구독은 이 기기에서도 끊는다(되살리지 않는다 — 서버가 409 로도 막는다).
// - 서버 세대가 바뀌었으면 기록에 맞추고, 12시간마다 키 · 시각을 서버에 다시 맞춘다.
// 결과: skip(해당 없음) | unverified(서버에 못 물음 — 화면은 '확인 못 함') | none | busy | dropped | confirmed | migrated
// | adopted | fresh | synced | changed | cancelled
export function syncPush({ userId, win = globalThis.window, now = Date.now() } = {}) {
  if (!userId || pushSupport(win) !== "supported") return Promise.resolve("skip");
  const current = captureAccountGuard({ accountOnly: true });
  return exclusive(async () => {
    if (!current()) return "cancelled";
    const moved = await migrateLegacy({ userId, win, current });
    if (moved === "later") return "unverified"; // 서버에 못 물었다 — 화면은 '확인 못 함', 다음 기회에
    const subscription = await currentSubscription(win);
    if (!current()) return "cancelled";
    const record = readRecord();
    if (!subscription) {
      if (record) clearRecord();
      return "none";
    }
    if (record?.pending && record.user === String(userId) && record.endpoint === subscription.endpoint) {
      const locks = typeof globalThis.navigator?.locks?.request === "function";
      if (inflight.has(record.op) || (!locks && now - Number(record.startedAt || 0) < PENDING_STALE_MS)) return "busy";
      const server = await serverStatus(userId, subscription.endpoint, { force: true, now });
      if (!current()) return "cancelled";
      if (server === null) return "unverified";
      if (readRecord()?.op !== record.op) return "changed";
      if (!server.subscribed) {
        await dropLocal({ local: true, win });
        return "dropped";
      }
      writeRecord({ user: String(userId), endpoint: subscription.endpoint, version: server.version, syncedAt: now });
      return "confirmed";
    }
    if (!owns(record, userId, subscription.endpoint)) {
      await dropLocal({ local: true, win });
      return "dropped";
    }
    if (win.Notification.permission !== "granted") return "skip";
    const due = moved === "migrated" || now - Number(record.syncedAt || 0) >= SYNC_EVERY_MS;
    // 방금 옮기며 서버에 물었으면 그 답을 다시 쓴다(같은 주소 · 세대를 두 번 묻지 않게).
    const server = await serverStatus(userId, subscription.endpoint, { force: due && moved !== "migrated", now });
    if (server === null) return "unverified"; // 서버에 못 물었다 — 다음 기회에
    const latest = await currentSubscription(win);
    // 마지막 기다림 바로 뒤 — 여기서부터 서버에 쓰기까지 기다림이 없다(계정이 바뀌었으면 B 의 토큰으로 A 의 구독을 등록하게 된다).
    if (!current()) return "cancelled";
    const mine = readRecord();
    if (latest?.endpoint !== subscription.endpoint || !owns(mine, userId, subscription.endpoint)) return "changed";
    if (!server.subscribed) {
      await dropLocal({ local: true, win });
      return "dropped";
    }
    const adopted = server.version !== mine.version;
    if (adopted) writeRecord({ ...mine, version: server.version }); // 서버가 확인한 세대로 기록을 맞춘다
    if (!due) return adopted ? "adopted" : "fresh";
    const body = subscription.toJSON();
    let saved;
    try {
      // 서버가 방금 알려 준 세대를 기대한다 — 그사이 다른 계정이 가져갔으면 409.
      saved = await api.pushSubscribe({ endpoint: body.endpoint, keys: body.keys, platform: currentDevice(win).platform,
        expected_version: server.version });
    } catch (error) {
      if (error?.status !== 409) throw error;
      await dropLocal({ local: true, win });
      return "dropped";
    }
    if (!current() || !owns(readRecord(), userId, subscription.endpoint)) return "cancelled"; // 등록하는 사이 바뀌었다
    writeRecord({ user: String(userId), endpoint: subscription.endpoint, version: Number(saved?.version) || server.version,
      syncedAt: now });
    return moved === "migrated" ? "migrated" : "synced";
  });
}

// 앱으로 처음 열었을 때 한 번 묻는 조건 — 로그인했고, 앱(홈 화면)으로 열었고, 이 기기에서 꺼져 있고, 권한을 아직 안 물었고,
// 이 기기에서 한 번도 답하지 않았고, 로그인 · 거래소 연결처럼 다른 일을 하는 화면이 아닐 때.
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
