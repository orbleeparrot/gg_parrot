// 헤더 종 아이콘의 상태 — 안 읽은 수는 60초마다·탭이 다시 보일 때 가볍게 묻고,
// 목록은 패널을 열 때 읽는다. 읽음 처리는 화면에 먼저 반영하고 서버 응답으로 맞춘다.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { getToken } from "../lib/auth.js";
import { markAllReadLocal, markReadLocal, unreadAfter } from "../lib/notifications.js";

export const UNREAD_POLL_MS = 60_000;

export default function useNotifications(token) {
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState(null); // null = 아직 안 불러옴
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const unreadRef = useRef(unread);
  unreadRef.current = unread;

  // 응답이 돌아왔을 때 아직 같은 계정인지 — 로그아웃·계정 전환 뒤의 늦은 응답은 버린다.
  const current = useCallback(() => Boolean(token) && getToken() === token, [token]);

  const refreshUnread = useCallback(async () => {
    if (!token) return;
    try {
      const data = await api.myNotificationsUnread();
      if (current()) setUnread(Number(data.unread) || 0);
    } catch (_) {
      // 배지는 다음 주기에 다시 묻는다.
    }
  }, [token, current]);

  useEffect(() => {
    if (!token) {
      setUnread(0);
      setItems(null);
      setError("");
      return undefined;
    }
    refreshUnread();
    const timer = setInterval(refreshUnread, UNREAD_POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") refreshUnread();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [token, refreshUnread]);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError("");
    try {
      const data = await api.myNotifications();
      if (!current()) return;
      setItems(Array.isArray(data.items) ? data.items : []);
      setUnread(Number(data.unread) || 0);
    } catch (reason) {
      if (current()) setError(String(reason?.message || "알림을 불러오지 못했어요."));
    } finally {
      if (current()) setLoading(false);
    }
  }, [token, current]);

  const markRead = useCallback(async (ids) => {
    const wanted = (ids || []).filter((id) => Number.isInteger(id));
    if (!wanted.length) return;
    setUnread(unreadAfter(unreadRef.current, itemsRef.current, wanted));
    setItems((prev) => (prev ? markReadLocal(prev, wanted) : prev));
    try {
      const data = await api.readNotifications({ ids: wanted });
      if (current()) setUnread(Number(data.unread) || 0);
    } catch (_) {
      // 화면은 이미 읽음으로 보인다; 다음 목록 조회에서 서버 상태로 돌아온다.
    }
  }, [current]);

  const markAll = useCallback(async () => {
    setUnread(0);
    setItems((prev) => (prev ? markAllReadLocal(prev) : prev));
    try {
      const data = await api.readNotifications({ all: true });
      if (current()) setUnread(Number(data.unread) || 0);
    } catch (_) {
      // 위와 같다.
    }
  }, [current]);

  return { unread, items, loading, error, load, markRead, markAll, refreshUnread };
}
