import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../lib/auth.js";
import { disablePush, enablePush, loadPushConfig, readPushState, sendTestPush } from "../lib/push.js";

// 이 기기의 앱 알림 상태 — 설정 탭 · 내 에이전트 종 단추 · 첫 실행 묻기가 함께 쓴다.
// 한 곳에서 켜고 끄면 CHANGED 로 다른 곳도 다시 읽는다.
// state: loading | on | off | denied | unconfigured | desktop | needs-install | in-app | unsupported
const CHANGED = "ggp:push-changed";

export function usePushState() {
  const { token, user } = useAuth();
  const userId = token && user?.id != null ? String(user.id) : "";
  const [config, setConfig] = useState(null);
  const [state, setState] = useState("loading");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    loadPushConfig().then((value) => {
      if (alive) setConfig(value);
    });
    return () => { alive = false; };
  }, []);

  const refresh = useCallback(() => {
    if (!config) return;
    readPushState({ userId, config }).then(setState).catch(() => setState("unsupported"));
  }, [config, userId]);

  useEffect(() => {
    refresh();
    const onVisible = () => {
      if (document.visibilityState === "visible") refresh(); // 브라우저 설정에서 알림을 막거나 풀고 돌아왔을 때
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
        window.dispatchEvent(new Event(CHANGED));
        return result;
      })
      .finally(() => setBusy(false));
  }, [config, userId]);

  const disable = useCallback(() => {
    setBusy(true);
    return disablePush()
      .catch(() => "error")
      .then((result) => {
        window.dispatchEvent(new Event(CHANGED));
        return result;
      })
      .finally(() => setBusy(false));
  }, []);

  const test = useCallback(() => {
    setBusy(true);
    return sendTestPush().finally(() => setBusy(false));
  }, []);

  return { state, busy, enable, disable, test, signedIn: Boolean(userId) };
}

// 권한을 막았을 때 푸는 길 — 기기마다 다르다(아이폰 홈 화면 앱은 iOS 설정).
export function unblockHint(platform) {
  return platform === "ios"
    ? ["설정", "알림", "껄무새", "알림 허용"]
    : ["주소창 왼쪽 사이트 정보", "알림", "허용"];
}

// 켜기 결과를 토스트로 — 켜짐은 완료, 막힘 · 실패는 오류. 권한 창을 닫기만 한 것(dismissed)은 알리지 않는다.
export function enableToast(result) {
  if (result === "on") return { tone: "done", label: "알림", title: "앱 알림을 켰어요" };
  if (result === "denied") return { tone: "error", label: "알림", title: "알림이 막혀 있어요", body: "설정 › 앱 알림에서 푸는 방법을 볼 수 있어요." };
  if (result === "dismissed" || result === "signed-out") return null;
  return { tone: "error", label: "실패", title: "앱 알림을 켜지 못했어요", body: "잠시 뒤에 다시 시도해 주세요." };
}
