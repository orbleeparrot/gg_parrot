// 헤더 종 아이콘의 상태. 안 읽은 수는 세 겹으로 맞춘다:
//  1) 내 행동 직후 — api.js 가 쓰기 요청 뒤에 내는 ggp:activity 를 듣고 바로 다시 묻는다.
//  2) 서버 푸시 — SSE(/api/me/notifications/stream)가 바뀔 때마다 안 읽은 수를 보낸다.
//     끊기면 1초부터 두 배씩(최대 30초) 기다렸다가 새 토큰으로 다시 붙고, 탭이 숨으면 닫는다.
//  3) 폴링 — 적응형 폴러(채팅과 같은 것). 스트림이 없으면 20초, 붙어 있으면 2분 안전망.
//     오류 시 지수 백오프, 숨김 시 중단, 탭 복귀 시 즉시 조회.
// 새 알림은 토스트로도 띄운다: SSE 의 notification 이벤트로 본문이 오고, 스트림이 없을 때는
// 폴링 응답의 latest_id 가 마지막으로 본 id 보다 크면 그 뒤를 받아 온다(after). 패널이 열려 있으면 띄우지 않는다.
// 목록은 패널을 열 때 읽고, 열려 있는 동안 위 신호가 오면 조용히 다시 읽는다.
// 패널을 여는 것 자체가 읽음이다: 열 때(그리고 열린 채로 새로 들어올 때) 서버에 모두 읽음을 보내
// 배지를 지운다. 방금 받아 온 목록의 안 읽음 점은 이번 열람 동안만 남겨 무엇이 새것인지 보여 준다.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { getToken } from "../lib/auth.js";
import {
  ACTIVITY_EVENT, appendToast, hasNewerNotifications, markAllReadLocal, markReadLocal, parseNotificationEvent,
  parseUnreadEvent, streamRetryDelay, unreadAfter,
} from "../lib/notifications.js";
import useAdaptivePolling from "./useAdaptivePolling.js";

export const POLL_MS = 20_000;
export const POLL_LIVE_MS = 120_000;
export const POLL_MAX_MS = 180_000;
export const ACTIVITY_DEBOUNCE_MS = 300;

export default function useNotifications(token) {
  const [unread, setUnread] = useState(0);
  const [items, setItems] = useState(null); // null = 아직 안 불러옴(패널을 연 적 없음)
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [live, setLive] = useState(false); // SSE 가 붙어 있는지
  const [panelOpen, setPanelOpen] = useState(false);
  const [toasts, setToasts] = useState([]);
  const lastSeenRef = useRef(null); // 토스트로 띄웠거나 이미 알고 있던 가장 큰 알림 id
  const toastKeyRef = useRef(0);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const openRef = useRef(panelOpen);
  openRef.current = panelOpen;
  // 이번 열람에서 '새것'이었던 알림 id — 서버가 읽음 처리한 뒤 목록을 다시 받아도 점을 유지한다. 닫으면 비운다.
  const freshRef = useRef(new Set());
  const unreadRef = useRef(unread);
  unreadRef.current = unread;
  const liveRef = useRef(live);
  liveRef.current = live;

  // 응답이 돌아왔을 때 아직 같은 계정인지 — 로그아웃·계정 전환 뒤의 늦은 응답은 버린다.
  const current = useCallback(() => Boolean(token) && getToken() === token, [token]);

  const noteSeen = useCallback((id) => {
    const value = Number(id);
    if (!Number.isFinite(value)) return;
    if (lastSeenRef.current === null || value > lastSeenRef.current) lastSeenRef.current = value;
  }, []);
  const showToast = useCallback((item) => {
    noteSeen(item.id);
    if (openRef.current || item.read) return; // 패널을 보고 있으면 토스트는 군더더기
    toastKeyRef.current += 1;
    setToasts((prev) => appendToast(prev, item, { key: toastKeyRef.current }));
  }, [noteSeen]);
  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const load = useCallback(async ({ silent = false, markRead = false } = {}) => {
    if (!token) return;
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await api.myNotifications();
      if (!current()) return;
      let list = Array.isArray(data.items) ? data.items : [];
      for (const item of list) noteSeen(item.id);
      if (openRef.current) {
        const fresh = freshRef.current;
        for (const item of list) if (!item.read) fresh.add(item.id);
        list = list.map((item) => (fresh.has(item.id) && item.read ? { ...item, read: false } : item));
      }
      setItems(list);
      setUnread(Number(data.unread) || 0);
      if (markRead && Number(data.unread) > 0) {
        // 열어서 봤으니 읽음 — 서버 상태를 맞추고 배지를 지운다(목록의 점은 이번 열람 동안 유지).
        const done = await api.readNotifications({ all: true });
        if (current()) setUnread(Number(done.unread) || 0);
      }
    } catch (reason) {
      if (current() && !silent) setError(String(reason?.message || "알림을 불러오지 못했어요."));
    } finally {
      if (current() && !silent) setLoading(false);
    }
  }, [token, current, noteSeen]);

  // 3) 폴링 — 새 id 가 보이면(스트림이 없을 때) 그 뒤를 받아 토스트로 띄운다
  const poll = useCallback(async (signal) => {
    if (!token) return { nextPollMs: POLL_MS };
    const data = await api.myNotificationsUnread({ signal });
    if (!current()) return { nextPollMs: POLL_MS };
    setUnread(Number(data.unread) || 0);
    if (hasNewerNotifications(data.latest_id, lastSeenRef.current)) {
      try {
        const fresh = await api.myNotifications({ after: lastSeenRef.current, signal });
        if (current()) for (const item of fresh.items || []) showToast(item);
      } catch (_) {
        // 다음 폴링에서 다시 시도한다
      }
    }
    noteSeen(data.latest_id);
    return { nextPollMs: liveRef.current ? POLL_LIVE_MS : POLL_MS };
  }, [token, current, noteSeen, showToast]);
  const refresh = useAdaptivePolling(poll, {
    intervalMs: POLL_MS, maxIntervalMs: POLL_MAX_MS, enabled: Boolean(token), immediate: true, pollKey: token || "",
  });

  useEffect(() => {
    if (!token) {
      setUnread(0);
      setItems(null);
      setError("");
      setLive(false);
      setToasts([]);
    }
    lastSeenRef.current = null;
  }, [token]);

  // 패널을 열면 목록을 읽고 그 시점의 알림을 모두 읽음 처리한다. 닫으면 '새것' 표시를 비운다.
  useEffect(() => {
    if (!token || !panelOpen) {
      freshRef.current = new Set();
      return;
    }
    load({ markRead: true });
  }, [token, panelOpen, load]);

  // 1) 내 행동 직후
  useEffect(() => {
    if (!token) return undefined;
    let timer = null;
    const onActivity = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        refresh();
        if (openRef.current) load({ silent: true, markRead: true });
      }, ACTIVITY_DEBOUNCE_MS);
    };
    window.addEventListener(ACTIVITY_EVENT, onActivity);
    return () => {
      clearTimeout(timer);
      window.removeEventListener(ACTIVITY_EVENT, onActivity);
    };
  }, [token, refresh, load]);

  // 2) 서버 푸시(SSE)
  useEffect(() => {
    if (!token || typeof EventSource === "undefined") return undefined;
    let source = null;
    let timer = null;
    let attempt = 0;
    let closed = false;
    const drop = () => {
      if (source) {
        source.close();
        source = null;
      }
      setLive(false);
    };
    const schedule = () => {
      if (closed) return;
      clearTimeout(timer);
      timer = setTimeout(connect, streamRetryDelay(attempt));
      attempt += 1;
    };
    async function connect() {
      if (closed || document.visibilityState === "hidden") return;
      let credential;
      try {
        credential = await api.notificationsStreamToken();
      } catch (_) {
        schedule();
        return;
      }
      if (closed || !current() || !credential?.token) return;
      drop();
      source = new EventSource(api.notificationsStreamUrl(credential.token));
      source.onopen = () => {
        attempt = 0;
        setLive(true);
      };
      source.addEventListener("notification", (event) => {
        const item = parseNotificationEvent(event.data);
        if (item && current()) showToast(item);
      });
      source.addEventListener("unread", (event) => {
        const count = parseUnreadEvent(event.data);
        if (count === null || !current()) return;
        setUnread(count);
        if (openRef.current) load({ silent: true, markRead: true });
      });
      // 토큰 만료·서버 재시작·네트워크 끊김: 우리가 닫고 새 토큰으로 다시 붙는다(EventSource 의 자동 재연결은 옛 토큰을 쓴다).
      source.onerror = () => {
        drop();
        schedule();
      };
    }
    const onVisibility = () => {
      if (document.visibilityState === "hidden") {
        clearTimeout(timer);
        drop();
      } else {
        attempt = 0;
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);
    connect();
    return () => {
      closed = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      drop();
    };
  }, [token, current, load, showToast]);

  const markRead = useCallback(async (ids) => {
    const wanted = (ids || []).filter((id) => Number.isInteger(id));
    if (!wanted.length) return;
    setUnread(unreadAfter(unreadRef.current, itemsRef.current, wanted));
    setItems((prev) => (prev ? markReadLocal(prev, wanted) : prev));
    try {
      const data = await api.readNotifications({ ids: wanted });
      if (current()) setUnread(Number(data.unread) || 0);
    } catch (_) {
      // 화면은 이미 읽음으로 보인다; 다음 조회에서 서버 상태로 돌아온다.
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

  return {
    unread, items, loading, error, live, load, markRead, markAll, refreshUnread: refresh, setPanelOpen,
    toasts, dismissToast,
  };
}
