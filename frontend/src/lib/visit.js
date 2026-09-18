// 방문 비콘 — 관리자 대시보드 '사용자 지표'의 원천(세션 · 페이지뷰 · 체류 · 유입 · 기기).
// 개인정보는 보내지 않는다: 브라우저에 남는 무작위 id 하나(서버는 해시만 저장), 경로, 유입 출처 URL,
// utm_source, 화면 너비. 채널·기기 분류는 서버 몫 — 여기서도 판단하면 규칙이 두 군데로 갈라진다.
//
// 세션: sessionStorage `ggp:session` {key, last_ms, started_ms, utm}. 30분 넘게 조용하면 새 키 + 랜딩.
// 신규: localStorage `ggp:first_seen` 이 이번 세션 안에서 만들어졌으면 is_new (세션 내내 유지).
// 체류: 뷰마다 view_key 를 새로 뽑고, 페이지를 떠날 때(pagehide · 숨김 · 다음 화면으로 이동) `/api/visit/leave` 에
// dwell_ms 를 sendBeacon 으로 보낸다. 보이는 동안은 60초마다 같은 비콘을 하트비트로 보낸다 — 서버는 max(기존, 값) 이라
// 늦게 도착해도, 마지막 비콘이 유실돼도 안전하다(유실되면 그 세션이 0초로 들어가 평균을 끌어내리던 문제).
//
// 저장소가 막힌 브라우저(프라이빗 · 쿠키 차단)에서는 페이지뷰마다 새 방문자·새 세션이 되어 DAU 가 최대 5배로 부풀었다.
// 그래서 방문자 id · 첫 방문 시각 · 세션은 모듈 메모리에도 들고 있고(탭 수명 동안 재사용), 저장소는 새로고침·다음 방문을
// 위한 보관용이다. 방문자 id 가 저장소를 한 번도 통과하지 못하면 빈 값을 보낸다 — 다음 페이지 로드가 또 새 id 를 만들
// 것이므로, 세지 않는 편이 부풀리는 것보다 낫다(서버는 빈 visitor 를 활성 사용자에서 뺀다).
import { getToken } from "./auth.js";

export const VISITOR_KEY = "ggp:visitor";
export const FIRST_SEEN_KEY = "ggp:first_seen";
export const SESSION_KEY = "ggp:session";
export const SESSION_IDLE_MS = 30 * 60 * 1000;
export const MAX_DWELL_MS = 6 * 60 * 60 * 1000;
export const HEARTBEAT_MS = 60 * 1000;
// 뷰 기록은 이만큼 기다렸다 보낸다 — 권한 없는 /admin → /mypage 처럼 그 사이 주소가 바뀌면 유령 뷰라 기록하지 않는다.
export const VIEW_SETTLE_MS = 400;
// 같은 경로를 이 안에 다시 기록하려는 호출은 StrictMode 의 이중 effect — 한 번만 센다.
export const REPEAT_GUARD_MS = 1000;
// 화면이 아니라 즉시 다른 곳으로 보내는 경로 — 뷰가 아니다(/runner/install 은 실제 화면이라 정확히 일치할 때만).
export const SKIPPED_PATHS = ["/gallery", "/runner"];

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

export function cleanPath(pathname) {
  return (String(pathname || "/").split(/[?#]/, 1)[0] || "/").slice(0, 120);
}

export function isSkippedPath(pathname) {
  return SKIPPED_PATHS.includes(cleanPath(pathname));
}

// 순수: 직전 호출과 같은 경로가 1초 안에 다시 오면 무시(StrictMode 이중 effect).
export function isRepeatCall(last, pathname, now, guardMs = REPEAT_GUARD_MS) {
  return Boolean(last) && last.pathname === pathname && now - last.at < guardMs;
}

// 순수: 하트비트를 보낼 조건 — 뷰가 있고, 아직 떠나지 않았고, 화면이 보일 때. 숨긴 탭은 체류가 아니다.
export function heartbeatDue(current, visibilityState) {
  return Boolean(current) && !current.left && visibilityState !== "hidden";
}

// 순수: 이번 뷰의 체류(ms). 상한 6시간 — 탭을 켜 둔 채 잔 경우.
export function dwellOf(current, now) {
  if (!current) return 0;
  return Math.min(MAX_DWELL_MS, Math.max(0, now - current.startedMs));
}

// KST 날짜(YYYY-MM-DD). 서머타임이 없어 UTC+9 고정 계산으로 충분하다.
export function kstDay(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n)) return "";
  return new Date(n + 9 * 60 * 60 * 1000).toISOString().slice(0, 10);
}

// 리더보드 노출·열람 dedupe 키 — 세션당 매크로 1회지만 자정을 넘긴 세션은 다음 날 다시 1회.
// 서버의 일별 노출 집계(day_kst)와 같은 경계를 쓰지 않으면 자정 뒤 노출이 어제 것에 묻혀 사라진다.
export function impressionKey(id, ms) {
  return `${kstDay(ms)}:${id}`;
}

// 순수: 서버 계약대로 body 를 만든다. path 는 쿼리·해시를 뗀 경로(`:id` 정규화는 서버).
export function visitPayload(pathname, {
  kind = "view", referrer = "", search = "", utm = "", visitor = "", viewKey = "", sessionKey = "",
  isNew = false, isLanding = false, screenW = 0,
} = {}) {
  return {
    kind,
    path: cleanPath(pathname),
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
  setTimer = (fn, ms) => globalThis.setTimeout(fn, ms), clearTimer = (id) => globalThis.clearTimeout(id),
} = {}) {
  let current = null; // { viewKey, startedMs, left, hiddenAt }
  let listening = false;
  // 모듈 메모리 캐시(탭 수명). null = 아직 안 정함. memVisitor "" = 저장소가 완전히 막혀 방문자를 셀 수 없음.
  let memVisitor = null;
  let memFirstSeen = null;
  let memSession = null;
  let pending = null; // { pathname, timer } — 400ms 기다리는 뷰
  let lastCall = null; // { pathname, at } — 이중 effect 판정
  let heartbeat = null;

  function read(storage, key) {
    try { return storage?.getItem?.(key) ?? null; } catch { return null; }
  }
  function write(storage, key, value) {
    try { storage?.setItem?.(key, value); } catch { /* 메모리 캐시가 이번 탭은 지킨다 */ }
  }

  function visitorId() {
    if (memVisitor !== null) return memVisitor;
    let id = read(local, VISITOR_KEY);
    if (!id) {
      id = makeKey();
      write(local, VISITOR_KEY, id);
      // 읽기·쓰기가 모두 실패하면 다음 페이지 로드가 또 새 id 를 만든다 — 빈 값을 보내 세지 않게 한다.
      if (read(local, VISITOR_KEY) !== id) id = "";
    }
    memVisitor = id;
    return id;
  }

  function firstSeen(nowMs) {
    if (memFirstSeen !== null) return memFirstSeen;
    const stored = Number(read(local, FIRST_SEEN_KEY));
    if (Number.isFinite(stored) && stored > 0) {
      memFirstSeen = stored;
      return stored;
    }
    write(local, FIRST_SEEN_KEY, String(nowMs));
    memFirstSeen = nowMs;
    return nowMs;
  }

  function readSession() {
    if (memSession) return memSession;
    try { return JSON.parse(read(session, SESSION_KEY) || "null"); } catch { return null; }
  }

  function writeSession(next) {
    memSession = { key: next.key, last_ms: next.last_ms, started_ms: next.started_ms, utm: next.utm || "" };
    write(session, SESSION_KEY, JSON.stringify(memSession));
  }

  function touchSession(nowMs, utmNow) {
    const next = sessionState(nowMs, readSession(), makeKey);
    // 캠페인으로 들어온 세션은 이후 화면에도 같은 utm 을 실어 채널이 세션 내내 같게 읽히도록.
    if (utmNow) next.utm = utmNow;
    writeSession(next);
    return next;
  }

  // 화면을 보는 동안에도 세션이 살아 있다고 적어 둔다 — 31분 읽은 뒤 클릭이 새 세션으로 쪼개지지 않게.
  function keepSessionAlive(nowMs) {
    const s = readSession();
    if (!s || typeof s.key !== "string" || !s.key) return;
    writeSession({ ...s, last_ms: nowMs });
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

  function sendDwell(dwell) {
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

  function stopHeartbeat() {
    if (heartbeat === null) return;
    clearTimer(heartbeat);
    heartbeat = null;
  }

  function heartbeatTick() {
    heartbeat = null;
    if (!heartbeatDue(current, doc?.visibilityState)) return;
    const t = now();
    keepSessionAlive(t);
    sendDwell(dwellOf(current, t));
    heartbeat = setTimer(heartbeatTick, HEARTBEAT_MS);
  }

  function startHeartbeat() {
    stopHeartbeat();
    heartbeat = setTimer(heartbeatTick, HEARTBEAT_MS);
  }

  function leave() {
    if (!current || current.left) return;
    const t = now();
    current.left = true;
    current.hiddenAt = t;
    stopHeartbeat();
    keepSessionAlive(t);
    sendDwell(dwellOf(current, t));
  }

  // 탭이 다시 보이면 같은 뷰를 이어 간다. 숨어 있던 시간은 체류가 아니므로 startedMs 를 그만큼 민다 —
  // 그래야 다음 leave 의 dwell(now - startedMs) 에서 그 구간이 빠진다(80배로 부풀던 원인).
  function resume() {
    if (!current || !current.left) return;
    const t = now();
    current.startedMs += Math.max(0, t - current.hiddenAt);
    current.left = false;
    startHeartbeat();
  }

  function listen() {
    if (listening) return;
    listening = true;
    win?.addEventListener?.("pagehide", leave);
    doc?.addEventListener?.("visibilitychange", () => { if (doc.visibilityState === "hidden") leave(); else resume(); });
  }

  function context(nowMs, utmNow = "") {
    const s = touchSession(nowMs, utmNow);
    const first = firstSeen(nowMs);
    return { session: s, isNew: isNewVisitor(first, s.started_ms) };
  }

  function startView(pathname) {
    leave(); // 직전 화면의 체류 시간 — SPA 이동은 pagehide 가 안 뜬다.
    const t = now();
    const search = win?.location?.search || "";
    const { session: s, isNew } = context(t, utmFromSearch(search));
    const viewKey = makeKey();
    const visitor = visitorId();
    current = { viewKey, startedMs: t, left: false, hiddenAt: 0 };
    listen();
    // 숨긴 채 시작한 뷰(cmd-클릭 · 새 탭에서 열기)는 '이미 떠난' 상태로 둔다 — 그래야 나중의 visible 이벤트가
    // resume() 을 타서 숨어 있던 구간을 startedMs 에서 빼고 하트비트를 시작한다. 그러지 않으면 첫 하트비트가
    // 숨김 판정으로 조용히 끝나 다시 잡히지 않고, resume() 도 left 가 false 라 돌아가서 백그라운드 탭에 둔
    // 시간 전체(30분이면 30분)가 체류로 들어갔다.
    if (doc?.visibilityState === "hidden") {
      current.left = true;
      current.hiddenAt = t;
    } else {
      startHeartbeat();
    }
    send(visitPayload(pathname, {
      kind: "view", referrer: doc?.referrer || "", search, utm: s.utm, visitor, viewKey, sessionKey: s.key,
      // 방문자를 못 세는 브라우저는 첫 방문인지도 알 수 없다 — 신규로 지어내지 않는다.
      isNew: visitor ? isNew : false, isLanding: s.is_landing, screenW: win?.innerWidth || 0,
    }));
  }

  function cancelPending() {
    if (!pending) return;
    clearTimer(pending.timer);
    pending = null;
  }

  function recordVisit(pathname) {
    const path = cleanPath(pathname);
    if (isSkippedPath(path)) return;
    const t = now();
    if (isRepeatCall(lastCall, path, t)) return;
    lastCall = { pathname: path, at: t };
    cancelPending(); // 400ms 안에 다른 경로로 갔으면 앞 경로는 화면에 뜬 적이 없다.
    const timer = setTimer(() => {
      pending = null;
      // 타이머 사이에 주소가 이미 다른 곳이면(<Navigate replace> 리다이렉트) 유령 뷰 — 기록하지 않는다.
      const here = win?.location?.pathname;
      if (typeof here === "string" && here && cleanPath(here) !== path) return;
      startView(path);
    }, VIEW_SETTLE_MS);
    pending = { pathname: path, timer };
  }

  // 행동 이벤트(지금은 백테스트 실행만). view_key 는 새로 뽑는다 — 뷰 키를 다시 쓰면 서버가 중복으로 버린다.
  // 로그인하지 않아도 보낸다 — 퍼널은 방문자(visitor) 키로 잇는다.
  function recordEvent(name) {
    const t = now();
    const { session: s, isNew } = context(t);
    const visitor = visitorId();
    send(visitPayload(name, {
      kind: "event", utm: s.utm, visitor, viewKey: makeKey(), sessionKey: s.key,
      isNew: visitor ? isNew : false, isLanding: s.is_landing, screenW: win?.innerWidth || 0,
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
