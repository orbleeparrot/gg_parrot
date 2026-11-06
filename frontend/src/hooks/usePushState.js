import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../lib/auth.js";
import { disablePush, enablePush, forgetPushStatus, loadPushConfig, readPushState, sendTestPush } from "../lib/push.js";

// 이 기기의 앱 알림 상태 — 설정 탭 · 내 에이전트 종 단추 · 첫 실행 묻기가 함께 쓴다.
// 한 곳에서 켜고 끄면 CHANGED 로 다른 곳도 다시 읽는다.
// state: loading | on | off | denied | unconfigured | desktop | needs-install | in-app | unsupported
const CHANGED = "ggp:push-changed";

function changed() {
  window.dispatchEvent(new Event(CHANGED));
}

export function usePushState() {
  const { token, user } = useAuth();
  const userId = token && user?.id != null ? String(user.id) : "";
  const [config, setConfig] = useState(null);
  const [state, setState] = useState("loading");
  const [busy, setBusy] = useState(false);
  const reads = useRef(0);

  useEffect(() => {
    let alive = true;
    loadPushConfig().then((value) => {
      if (alive) setConfig(value);
    });
    return () => { alive = false; };
  }, []);

  const refresh = useCallback(() => {
    if (!config) return;
    // 서버에 묻는 동안 계정이 바뀌거나 다시 읽으면 먼저 시작한 답은 버린다(늦게 온 옛 답이 새 상태를 덮지 않게).
    const seq = reads.current + 1;
    reads.current = seq;
    readPushState({ userId, config })
      .catch(() => "unsupported")
      .then((value) => {
        if (reads.current === seq) setState(value);
      });
  }, [config, userId]);

  useEffect(() => {
    refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh(); // 브라우저 설정에서 알림을 막거나 풀고 돌아왔을 때 · 서버가 기기를 지웠을 때
    };
    window.addEventListener(CHANGED, refresh);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener(CHANGED, refresh);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [refresh]);

  // 클릭 처리기에서 '바로' 불러야 권한 창이 뜬다 — 그래서 async 함수로 감싸지 않고 enablePush 를 먼저 부른다.
  const enable = useCallback(() => {
    if (!userId) return Promise.resolve("signed-out");
    setBusy(true);
    return enablePush({ userId, config })
      .catch(() => "error")
      .then((result) => {
        changed();
        return result;
      })
      .finally(() => setBusy(false));
  }, [config, userId]);

  const disable = useCallback(() => {
    setBusy(true);
    return disablePush()
      .catch(() => "error")
      .then((result) => {
        changed();
        return result;
      })
      .finally(() => setBusy(false));
  }, []);

  // 다 못 갔으면(끝난 구독은 서버가 지운다) 서버에 다시 물어 스위치를 맞춘다.
  const test = useCallback(() => {
    setBusy(true);
    return sendTestPush()
      .then((result) => {
        if (!(Number(result?.sent) >= Number(result?.devices) && Number(result?.devices) > 0)) {
          forgetPushStatus();
          changed();
        }
        return result;
      }, (error) => {
        forgetPushStatus();
        changed();
        throw error;
      })
      .finally(() => setBusy(false));
  }, []);

  return { state, busy, enable, disable, test, signedIn: Boolean(userId) };
}

// 권한을 막았을 때 푸는 길 — 기기마다 다르다(아이폰 홈 화면 앱은 iOS 설정).
export function unblockHint(platform) {
  return platform === "ios"
    ? ["설정", "알림", "껄무새", "알림 허용"]
    : ["주소창 왼쪽 사이트 정보", "알림", "허용"];
}

// 켜기 결과를 토스트로 — 켜짐은 완료, 막힘 · 실패는 오류. 권한 창을 닫기만 한 것(dismissed) · 켜는 동안 계정이 바뀐 것은 알리지 않는다.
export function enableToast(result) {
  if (result === "on") return { tone: "done", label: "알림", title: "앱 알림을 켰어요" };
  if (result === "denied") return { tone: "error", label: "알림", title: "알림이 막혀 있어요", body: "설정 › 앱 알림에서 푸는 방법을 볼 수 있어요." };
  if (result === "dismissed" || result === "signed-out" || result === "cancelled") return null;
  return { tone: "error", label: "실패", title: "앱 알림을 켜지 못했어요", body: "잠시 뒤에 다시 시도해 주세요." };
}

// 시험 알림 결과(서버: sent · devices · retry) — 다 감 · 일부 · 못 감을 가른다. 0건을 '보냈어요'라고 하지 않는다.
export function testToast(result) {
  const sent = Number(result?.sent) || 0;
  const devices = Number(result?.devices) || 0;
  if (devices > 0 && sent >= devices) return { tone: "done", label: "알림", title: "시험 알림을 보냈어요", body: `기기 ${devices}대` };
  if (sent > 0) return { tone: "done", label: "알림", title: "시험 알림을 보냈어요", body: `기기 ${devices}대 중 ${sent}대` };
  if ((Number(result?.retry) || 0) > 0) {
    return { tone: "error", label: "실패", title: "시험 알림을 보내지 못했어요", body: "알림 서비스가 잠시 막혔어요. 잠시 뒤에 다시 보내 주세요." };
  }
  return { tone: "error", label: "실패", title: "시험 알림을 보내지 못했어요", body: "이 기기 알림이 끊겼어요. 알림을 다시 켜 주세요." };
}
