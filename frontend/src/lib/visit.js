// 방문 비콘 — 관리자 대시보드 '사용자 지표'의 원천(세션 · 페이지뷰 · 체류 · 유입 · 기기).
// 개인정보는 보내지 않는다: 브라우저에 남는 무작위 id 하나(서버는 해시만 저장), 경로, 유입 출처 URL,
// utm_source, 화면 너비. 채널·기기 분류는 서버 몫 — 여기서도 판단하면 규칙이 두 군데로 갈라진다.
//
// 세션: sessionStorage `ggp:session` {key, last_ms, started_ms, utm}. 30분 넘게 조용하면 새 키 + 랜딩.
// 신규: localStorage `ggp:first_seen` 이 이번 세션 안에서 만들어졌으면 is_new (세션 내내 유지).
// 체류: 뷰마다 view_key 를 새로 뽑고, 페이지를 떠날 때(pagehide · 숨김 · 다음 화면으로 이동) 한 번만
// `/api/visit/leave` 에 dwell_ms 를 sendBeacon 으로 보낸다. 서버는 max(기존, 값) 이라 늦게 도착해도 안전.
import { getToken } from "./auth.js";

export const VISITOR_KEY = "ggp:visitor";
export const FIRST_SEEN_KEY = "ggp:first_seen";
export const SESSION_KEY = "ggp:session";
export const SESSION_IDLE_MS = 30 * 60 * 1000;
export const MAX_DWELL_MS = 6 * 60 * 60 * 1000;

export function randomKey() {
  const raw = globalThis.crypto?.randomUUID?.() || `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  return raw.replace(/-/g, "");
}

// 순수: 저장된 세션과 지금 시각으로 이번 뷰의 세션을 정한다.
export function sessionState(now, stored, makeKey = randomKey) {
  const key = typeof stored?.key === "string" ? stored.key : "";
  const last = Number(stored?.last_ms);
  const alive = Boolean(key) && Number.isFinite(last) && now - last < SESSION_IDLE_MS;
  if (!alive) return { key: makeKey(), last_ms: now, started_ms: now, is_landing: true, utm: "" };
  const started = Number(stored?.started_ms);
  return {
    key,
    last_ms: now,
    started_ms: Number.isFinite(started) ? started : last,
    is_landing: false,
    utm: typeof stored?.utm === "string" ? stored.utm : "",
  };
}

// 첫 방문 시각이 이번 세션 안이면 신규 방문자.
export function isNewVisitor(firstSeenMs, sessionStartedMs) {
  const first = Number(firstSeenMs);
  const started = Number(sessionStartedMs);
  return Number.isFinite(first) && Number.isFinite(started) && first >= started;
}

export function utmFromSearch(search) {
  try {
    return (new URLSearchParams(String(search || "")).get("utm_source") || "").slice(0, 60);
  } catch {
    return "";
  }
}

// 순수: 서버 계약대로 body 를 만든다. path 는 쿼리·해시를 뗀 경로(`:id` 정규화는 서버).
export function visitPayload(pathname, {
  kind = "view", referrer = "", search = "", utm = "", visitor = "", viewKey = "", sessionKey = "",
  isNew = false, isLanding = false, screenW = 0,
} = {}) {
  return {
    kind,
    path: (String(pathname || "/").split(/[?#]/, 1)[0] || "/").slice(0, 120),
    view_key: String(viewKey || ""),
    session_key: String(sessionKey || ""),
    referrer: String(referrer || "").slice(0, 300),
    utm_source: (utmFromSearch(search) || String(utm || "")).slice(0, 60),
    visitor: String(visitor || ""),
    is_new: Boolean(isNew),
    is_landing: Boolean(isLanding),
    screen_w: Math.max(0, Math.round(Number(screenW) || 0)),
  };
}

// 저장소는 사파리 프라이빗 모드 등에서 접근 자체가 던진다 — 없는 것처럼 다룬다.
function safeStorage(get) {
  return {
    getItem(k) { try { return get()?.getItem(k) ?? null; } catch { return null; } },
    setItem(k, v) { try { get()?.setItem(k, v); } catch { /* 저장 못 해도 비콘은 보낸다 */ } },
  };
}

export function createVisitTracker({
  win, doc, nav, local, session, fetchFn, now = () => Date.now(), makeKey = randomKey, token = getToken,
} = {}) {
  let current = null; // { viewKey, startedMs, left }
  let listening = false;

  function visitorId() {
    let id = local.getItem(VISITOR_KEY);
    if (!id) {
      id = makeKey();
      local.setItem(VISITOR_KEY, id);
    }
    return id;
  }

  function firstSeen(nowMs) {
    const stored = Number(local.getItem(FIRST_SEEN_KEY));
    if (Number.isFinite(stored) && stored > 0) return stored;
    local.setItem(FIRST_SEEN_KEY, String(nowMs));
    return nowMs;
  }

  function touchSession(nowMs, utmNow) {
    let stored = null;
    try { stored = JSON.parse(session.getItem(SESSION_KEY) || "null"); } catch { stored = null; }
    const next = sessionState(nowMs, stored, makeKey);
    // 캠페인으로 들어온 세션은 이후 화면에도 같은 utm 을 실어 채널이 세션 내내 같게 읽히도록.
    if (utmNow) next.utm = utmNow;
    session.setItem(SESSION_KEY, JSON.stringify({ key: next.key, last_ms: next.last_ms, started_ms: next.started_ms, utm: next.utm }));
    return next;
  }

  function headers() {
    const t = token?.() || "";
    return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}) };
  }

  function send(payload) {
    try {
      const result = fetchFn("/api/visit", { method: "POST", headers: headers(), body: JSON.stringify(payload), keepalive: true });
      result?.catch?.(() => {});
    } catch { /* 비콘은 본 흐름을 절대 막지 않는다 */ }
  }

  function leave() {
    if (!current || current.left) return;
    current.left = true;
    const dwell = Math.min(MAX_DWELL_MS, Math.max(0, now() - current.startedMs));
    const body = JSON.stringify({ view_key: current.viewKey, dwell_ms: dwell });
    if (typeof nav?.sendBeacon === "function") {
      try {
        if (nav.sendBeacon("/api/visit/leave", new Blob([body], { type: "application/json" }))) return;
      } catch { /* 아래 fetch 로 대신 */ }
    }
    try {
      const result = fetchFn("/api/visit/leave", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true });
      result?.catch?.(() => {});
    } catch { /* 체류 시간 하나 잃는 것뿐 */ }
  }

  function listen() {
    if (listening) return;
    listening = true;
    win?.addEventListener?.("pagehide", leave);
    doc?.addEventListener?.("visibilitychange", () => { if (doc.visibilityState === "hidden") leave(); });
  }

  function context(nowMs, utmNow = "") {
    const s = touchSession(nowMs, utmNow);
    const first = firstSeen(nowMs);
    return { session: s, isNew: isNewVisitor(first, s.started_ms) };
  }

  function recordVisit(pathname) {
    leave(); // 직전 화면의 체류 시간 — SPA 이동은 pagehide 가 안 뜬다.
    const t = now();
    const search = win?.location?.search || "";
    const { session: s, isNew } = context(t, utmFromSearch(search));
    const viewKey = makeKey();
    current = { viewKey, startedMs: t, left: false };
    listen();
    send(visitPayload(pathname, {
      kind: "view", referrer: doc?.referrer || "", search, utm: s.utm, visitor: visitorId(), viewKey, sessionKey: s.key,
      isNew, isLanding: s.is_landing, screenW: win?.innerWidth || 0,
    }));
  }

  // 행동 이벤트(지금은 백테스트 실행만). view_key 는 새로 뽑는다 — 뷰 키를 다시 쓰면 서버가 중복으로 버린다.
  function recordEvent(name) {
    const t = now();
    const { session: s, isNew } = context(t);
    send(visitPayload(name, {
      kind: "event", utm: s.utm, visitor: visitorId(), viewKey: makeKey(), sessionKey: s.key,
      isNew, isLanding: s.is_landing, screenW: win?.innerWidth || 0,
    }));
  }

  return { recordVisit, recordEvent, leave, currentViewKey: () => current?.viewKey || "" };
}

// 개발 서버에서 백엔드 프록시가 없으면 보내지 않는다(콘솔이 404 로 어지러워지는 것뿐이지만).
const ENABLED = typeof window !== "undefined" && typeof fetch === "function"
  && !(import.meta.env?.DEV && !import.meta.env?.VITE_API_PROXY);

let tracker = null;
function defaultTracker() {
  if (!tracker) {
    tracker = createVisitTracker({
      win: window,
      doc: document,
      nav: navigator,
      local: safeStorage(() => localStorage),
      session: safeStorage(() => sessionStorage),
      fetchFn: (...args) => fetch(...args),
    });
  }
  return tracker;
}

export function recordVisit(pathname) {
  if (!ENABLED) return;
  defaultTracker().recordVisit(pathname);
}

export function recordEvent(name) {
  if (!ENABLED) return;
  defaultTracker().recordEvent(name);
}
