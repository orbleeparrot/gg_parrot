// 헤더 종 아이콘의 상태. 안 읽은 수는 세 겹으로 맞춘다:
//  1) 내 행동 직후 — api.js 가 쓰기 요청 뒤에 내는 ggp:activity 를 듣고 바로 다시 묻는다.
//  2) 서버 푸시 — SSE(/api/me/notifications/stream)가 바뀔 때마다 안 읽은 수를 보낸다.
//     끊기면 1초부터 두 배씩(최대 30초) 기다렸다가 새 토큰으로 다시 붙고, 탭이 숨으면 닫는다.
//  3) 폴링 — 적응형 폴러(채팅과 같은 것). 스트림이 없으면 20초, 붙어 있으면 2분 안전망.
//     오류 시 지수 백오프, 숨김 시 중단, 탭 복귀 시 즉시 조회.
// 목록은 패널을 열 때 읽고, 열려 있는 동안 위 신호가 오면 조용히 다시 읽는다.
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { getToken } from "../lib/auth.js";
import {
  ACTIVITY_EVENT, markAllReadLocal, markReadLocal, parseUnreadEvent, streamRetryDelay, unreadAfter,
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
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const unreadRef = useRef(unread);
  unreadRef.current = unread;
  const liveRef = useRef(live);
  liveRef.current = live;

  // 응답이 돌아왔을 때 아직 같은 계정인지 — 로그아웃·계정 전환 뒤의 늦은 응답은 버린다.
  const current = useCallback(() => Boolean(token) && getToken() === token, [token]);

  const load = useCallback(async ({ silent = false } = {}) => {
    if (!token) return;
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await api.myNotifications();
      if (!current()) return;
      setItems(Array.isArray(data.items) ? data.items : []);
      setUnread(Number(data.unread) || 0);
    } catch (reason) {
      if (current() && !silent) setError(String(reason?.message || "알림을 불러오지 못했어요."));
    } finally {
      if (current() && !silent) setLoading(false);
    }
  }, [token, current]);

  // 3) 폴링
  const poll = useCallback(async (signal) => {
    if (!token) return { nextPollMs: POLL_MS };
    const data = await api.myNotificationsUnread({ signal });
    if (current()) setUnread(Number(data.unread) || 0);
    return { nextPollMs: liveRef.current ? POLL_LIVE_MS : POLL_MS };
  }, [token, current]);
  const refresh = useAdaptivePolling(poll, {
    intervalMs: POLL_MS, maxIntervalMs: POLL_MAX_MS, enabled: Boolean(token), immediate: true, pollKey: token || "",
  });

  useEffect(() => {
    if (!token) {
      setUnread(0);
      setItems(null);
      setError("");
      setLive(false);
    }
  }, [token]);

  // 1) 내 행동 직후
  useEffect(() => {
    if (!token) return undefined;
    let timer = null;
    const onActivity = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        refresh();
        if (itemsRef.current !== null) load({ silent: true });
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
      source.addEventListener("unread", (event) => {
        const count = parseUnreadEvent(event.data);
        if (count === null || !current()) return;
        setUnread(count);
        if (itemsRef.current !== null) load({ silent: true });
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
  }, [token, current, load]);

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

  return { unread, items, loading, error, live, load, markRead, markAll, refreshUnread: refresh };
}
