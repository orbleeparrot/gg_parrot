// 앱 알림 상태를 한 곳에서 — 설정 탭 · 채팅 '알림' 단추 · 첫 실행 묻기 · 알림 다리(usePushBridge)가 모두 이 하나를 본다.
// 화면 · 동기화 · 저장소가 따로 '켜짐'을 판단하면 등록 중 상태나 오래된 기억을 켜짐으로 오인한다.
//
// - 켜짐은 서버가 받은 뒤에만(등록 중에는 busy · 기록은 pending). 조회 실패는 'unknown' — '꺼짐'과 다르다.
// - 복구 진입점은 reconcile 하나 — 시작 · 로그인 · 연결 복구 · 앱으로 돌아옴. 서버에 소유권 · 세대를 물어 기기 기록을
//   확정한 뒤(push.syncPush: 이전 형식 옮기기 · 남은 등록 중 기록 정리 · 세대 맞추기) 상태를 다시 읽는다.
// - 다른 창에는 바뀐 뒤에 알린다(BroadcastChannel, 없으면 storage 이벤트) — 받은 창은 서버 확인을 새로 하고 다시 읽는다.
import * as pushOps from "./push.js";

const CHANNEL = "ggp-push";

export function createPushController(ops = pushOps, { win, doc = globalThis.document } = {}) {
  const listeners = new Set();
  let snapshot = { state: "loading", busy: false, userId: "" };
  let config = null;
  let reads = 0;
  let started = false;
  let known = false;
  let channel = null;
  const target = win ?? globalThis.window;

  function emit(patch) {
    snapshot = { ...snapshot, ...patch };
    listeners.forEach((listener) => listener());
  }

  function announce() {
    try {
      channel?.postMessage({ type: "changed" });
    } catch {
      /* 닫힌 채널 */
    }
  }

  async function loadConfig(again) {
    if (!config || (again && config.failed)) config = await ops.loadPushConfig();
    return config;
  }

  // 늦게 온 옛 읽기는 버린다(순번) — 계정이 바뀌었거나 그 뒤에 다시 읽기 시작했으면.
  async function refresh({ reloadConfig = false } = {}) {
    const seq = ++reads;
    const userId = snapshot.userId;
    const current = await loadConfig(reloadConfig);
    const state = await ops.readPushState({ userId, config: current, win }).catch(() => "unknown");
    if (seq === reads && userId === snapshot.userId) emit({ state });
    return state;
  }

  function changedElsewhere() {
    ops.forgetPushStatus();
    refresh();
  }

  function start() {
    if (started) return;
    started = true;
    if (typeof BroadcastChannel === "function") {
      channel = new BroadcastChannel(CHANNEL);
      channel.onmessage = changedElsewhere;
      channel.unref?.(); // 시험(node) 이 채널 때문에 끝나지 않는 일이 없게
    }
    target?.addEventListener?.("online", () => reconcile());
    target?.addEventListener?.("storage", (event) => {
      if (!channel && (event.key === null || event.key === ops.RECORD_KEY)) changedElsewhere();
    });
    doc?.addEventListener?.("visibilitychange", () => {
      if (doc.visibilityState === "visible") reconcile(); // 브라우저 설정에서 권한을 바꾸고 돌아왔을 때 · 오래 열어 둔 앱
    });
  }

  // 기기 기록을 서버와 맞추고(로그인했을 때만) 상태를 다시 읽는다. 기록이 바뀌었으면 다른 창에도 알린다.
  // 서버에 못 물었으면(unverified) 그 결과를 그대로 '확인 못 함'으로 — 다시 읽은 값이 그보다 단정적(꺼짐)이면 덮지 않는다.
  async function reconcile() {
    const userId = snapshot.userId;
    const result = userId ? await ops.syncPush({ userId, win }).catch(() => "unverified") : "skip";
    const state = await refresh({ reloadConfig: true });
    if (result === "unverified" && userId === snapshot.userId && ["off", "loading"].includes(state)) emit({ state: "unknown" });
    if (["dropped", "migrated", "confirmed", "adopted", "none"].includes(result)) announce();
    return result;
  }

  function setUser(userId) {
    start();
    const id = userId ? String(userId) : "";
    if (id === snapshot.userId && known) return;
    known = true;
    if (id !== snapshot.userId) emit({ userId: id, state: "loading" });
    reconcile();
  }

  function settle(promise, { tell = true } = {}) {
    return promise
      .catch(() => "error")
      .then(async (result) => {
        await refresh();
        if (tell) announce();
        return result;
      })
      .finally(() => emit({ busy: false }));
  }

  // 클릭 처리기에서 '바로' 불러야 권한 창이 뜬다 — enablePush 를 기다림 없이 먼저 부른다.
  function enable() {
    const userId = snapshot.userId;
    if (!userId) return Promise.resolve("signed-out");
    emit({ busy: true });
    return settle(ops.enablePush({ userId, config, win }));
  }

  function disable() {
    emit({ busy: true });
    return settle(ops.disablePush({ win }));
  }

  // 다 못 갔으면(끝난 구독은 서버가 지운다) 서버에 다시 물어 스위치를 맞춘다.
  function test() {
    emit({ busy: true });
    return ops.sendTestPush()
      .then(async (result) => {
        if (!(Number(result?.sent) >= Number(result?.devices) && Number(result?.devices) > 0)) {
          ops.forgetPushStatus();
          await refresh();
          announce();
        }
        return result;
      }, async (error) => {
        ops.forgetPushStatus();
        await refresh();
        throw error;
      })
      .finally(() => emit({ busy: false }));
  }

  // 로그아웃 — 이 기기 구독을 끊는다(같은 기기의 다음 사람에게 앞 사람의 소식이 가지 않게). 서버에는 묻지 않는다.
  function logout() {
    return settle(ops.disablePush({ local: true, win }));
  }

  return {
    subscribe(listener) {
      start();
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    snapshot: () => snapshot,
    setUser, refresh, reconcile, enable, disable, test, logout,
    dispose() {
      channel?.close();
      channel = null;
    },
  };
}

export const pushController = createPushController();
