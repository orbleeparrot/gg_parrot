import { useEffect, useSyncExternalStore } from "react";
import { useAuth } from "../lib/auth.js";
import { pushController } from "../lib/pushController.js";

// 이 기기의 앱 알림 상태 — 설정 탭 · 내 에이전트 종 단추 · 첫 실행 묻기가 함께 쓴다. 상태는 pushController 하나가 갖고,
// 여기서는 구독만 한다(화면마다 따로 판단하지 않는다).
// state: loading | on | off | denied | unconfigured | unknown(확인 못 함) | desktop | needs-install | in-app | unsupported
export function usePushState() {
  const { token, user } = useAuth();
  const userId = token && user?.id != null ? String(user.id) : "";
  useEffect(() => {
    pushController.setUser(userId);
  }, [userId]);
  const snapshot = useSyncExternalStore(pushController.subscribe, pushController.snapshot, pushController.snapshot);
  // 계정이 막 바뀐 순간 — 앞 계정의 상태를 보여 주지 않는다.
  const state = snapshot.userId === userId ? snapshot.state : "loading";
  return {
    state, busy: snapshot.busy, signedIn: Boolean(userId),
    enable: pushController.enable, disable: pushController.disable, test: pushController.test,
  };
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
  if (result === "update-pending") {
    // 옛 버전 워커가 아직 일하고, 다른 창이 열려 있어 새 버전으로 못 바꿨다 — 그 상태로 켜면 알림이 와도 띄우지 못한다.
    return { tone: "error", label: "알림", title: "앱 업데이트 뒤에 켤 수 있어요", body: "열려 있는 다른 껄무새 창을 닫고 다시 켜 주세요." };
  }
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
