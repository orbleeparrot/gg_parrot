import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth.js";
import { openTarget } from "../lib/push.js";
import { pushController } from "../lib/pushController.js";

// 앱 알림과 화면 사이 — ① 알림을 누르면 열린 앱에서 그 화면으로 옮기고(옮겼다고 워커에 답한다 — 답이 없으면 워커가
// 창을 그 주소로 새로 연다: 오프라인 안내 화면 · 옛 화면), ② 로그인한 계정을 pushController 에 알려 기기 기록을 서버와
// 맞추게 하고(돌아옴 · 연결 복구 때도 컨트롤러가 맞춘다), ③ 로그아웃하면 이 기기 구독을 끊는다(같은 기기의 다음 사람에게
// 앞 사람의 소식이 가지 않게).
export function usePushBridge() {
  const navigate = useNavigate();
  const { token, user } = useAuth();
  const userId = token && user?.id != null ? String(user.id) : "";
  const previous = useRef(userId);

  useEffect(() => {
    const worker = globalThis.navigator?.serviceWorker;
    if (!worker?.addEventListener) return undefined;
    const onMessage = (event) => {
      const path = openTarget(event.data);
      if (!path) return;
      try {
        event.ports?.[0]?.postMessage({ opened: true });
      } catch {
        /* 워커가 이미 기다리기를 그만뒀다 */
      }
      navigate(path);
    };
    worker.addEventListener("message", onMessage);
    return () => worker.removeEventListener("message", onMessage);
  }, [navigate]);

  useEffect(() => {
    const before = previous.current;
    previous.current = userId;
    pushController.setUser(userId);
    if (!userId && before) pushController.logout();
  }, [userId]);
}
