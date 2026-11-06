import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth.js";
import { disablePush, openTarget, syncPush } from "../lib/push.js";

// 앱 알림과 화면 사이 — ① 알림을 누르면 열린 앱에서 그 화면으로 옮기고, ② 로그인하면 이 기기 등록을 맞추고,
// ③ 로그아웃하면 이 기기 구독을 끊는다(같은 기기의 다음 사람에게 앞 사람의 소식이 가지 않게).
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
      if (path) navigate(path);
    };
    worker.addEventListener("message", onMessage);
    return () => worker.removeEventListener("message", onMessage);
  }, [navigate]);

  useEffect(() => {
    const before = previous.current;
    previous.current = userId;
    if (userId) {
      syncPush({ userId }).catch(() => {});
    } else if (before) {
      disablePush({ local: true }).catch(() => {});
    }
  }, [userId]);
}
