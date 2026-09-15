// 알림 패널의 순수 도우미 — 종류 라벨, 상대 시각, 배지 글자, 읽음 처리의 로컬 반영.
// React 없이 node --test 로 검증한다(tests/notifications.test.js). 데이터 모양은
// backend/app/notifications.py 의 view() 와 같다: { id, kind, title, body, link, data, created_ms, read }.

export const KIND_LABELS = Object.freeze({
  quest: "퀘스트",
  macro_sold: "매크로 판매",
  macro_registered: "매크로 등록",
  comment: "댓글",
  reply: "답글",
  agent: "에이전트",
  admin: "관리자 메시지",
  notice: "공지사항",
});

export function kindLabel(kind) {
  return KIND_LABELS[kind] || "알림";
}

// 종 위 배지 — 0이면 비우고, 99를 넘으면 "99+".
export const UNREAD_BADGE_MAX = 99;
export function badgeText(unread) {
  const count = Math.max(0, Math.floor(Number(unread) || 0));
  if (!count) return "";
  return count > UNREAD_BADGE_MAX ? `${UNREAD_BADGE_MAX}+` : String(count);
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const KST_OFFSET = 9 * HOUR;

// "방금 전 · n분 전 · n시간 전 · n일 전", 일주일이 넘으면 KST 날짜(M월 D일).
export function relativeTime(ms, now = Date.now()) {
  const at = Number(ms);
  if (!Number.isFinite(at) || at <= 0) return "";
  const diff = Math.max(0, now - at);
  if (diff < MINUTE) return "방금 전";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}분 전`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}시간 전`;
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)}일 전`;
  const date = new Date(at + KST_OFFSET);
  return `${date.getUTCMonth() + 1}월 ${date.getUTCDate()}일`;
}

// 포인트가 붙은 알림(퀘스트·매크로 판매)의 획득 포인트. 없으면 0.
export function pointsOf(item) {
  const points = Number(item?.data?.points);
  return Number.isFinite(points) && points > 0 ? Math.floor(points) : 0;
}

export function markReadLocal(items, ids) {
  const wanted = new Set(ids || []);
  return (items || []).map((item) => (wanted.has(item.id) && !item.read ? { ...item, read: true } : item));
}

export function markAllReadLocal(items) {
  return (items || []).map((item) => (item.read ? item : { ...item, read: true }));
}

// 몇 개를 읽음 처리한 뒤 배지에 남을 수 — 목록에서 실제로 안 읽었던 것만 뺀다(0 아래로는 안 내려간다).
export function unreadAfter(unread, items, ids) {
  const wanted = new Set(ids || []);
  const cleared = (items || []).filter((item) => wanted.has(item.id) && !item.read).length;
  return Math.max(0, (Number(unread) || 0) - cleared);
}

// 앱 안 경로만 따라간다("/agents", "/board/12"). 바깥 주소·빈 값은 이동하지 않는다.
export function isInternalLink(link) {
  return typeof link === "string" && link.startsWith("/") && !link.startsWith("//");
}

// --- 실시간 갱신 -----------------------------------------------------------
// 로그인 계정의 쓰기 요청(백테스트·댓글·등록·언락…)이 끝나면 api.js 가 이 이벤트를 낸다.
// 알림 훅은 이를 듣고 안 읽은 수를 바로 다시 묻는다 — 내가 방금 한 일의 결과는 폴링을 기다리지 않는다.
export const ACTIVITY_EVENT = "ggp:activity";

// 알림 자체를 읽는 요청과 로그인·가입은 알림을 만들지 않으니 신호에서 뺀다.
export function shouldSignalActivity(method, path) {
  const verb = String(method || "GET").toUpperCase();
  if (verb === "GET" || verb === "HEAD" || verb === "OPTIONS") return false;
  const pathname = String(path || "").split("?")[0];
  return !pathname.startsWith("/api/me/notifications") && !pathname.startsWith("/api/auth/");
}

// SSE 재연결 간격: 1초부터 두 배씩, 최대 30초, ±20% 흔들림(모두 같은 순간에 다시 붙지 않게).
export const STREAM_RETRY_MAX_MS = 30_000;
export function streamRetryDelay(attempt, random = Math.random) {
  const step = Math.max(0, Math.floor(Number(attempt) || 0));
  const base = Math.min(STREAM_RETRY_MAX_MS, 1000 * 2 ** step);
  return Math.round(base * (0.8 + 0.4 * random()));
}

// 토스트 큐: 최신이 앞, 같은 id 는 한 번만, 최대 TOAST_MAX 개(넘치면 오래된 것부터 뺀다).
export const TOAST_MAX = 3;
export function appendToast(toasts, item, { max = TOAST_MAX, key = 0 } = {}) {
  const id = Number(item?.id);
  if (!Number.isFinite(id)) return toasts || [];
  const list = (toasts || []).filter((toast) => toast.id !== id);
  return [{ id, item, key }, ...list].slice(0, max);
}

// 폴링 모드에서 새 알림이 생겼는지 — 마지막으로 본 id 보다 큰 id 가 서버에 있으면 그 뒤를 가져온다.
export function hasNewerNotifications(latestId, lastSeenId) {
  const latest = Number(latestId);
  if (!Number.isFinite(latest)) return false;
  if (lastSeenId === null || lastSeenId === undefined) return false; // 처음 본 값은 기준만 잡는다
  return latest > Number(lastSeenId);
}

// 서버가 보낸 `event: notification` 의 data(알림 한 건). 모양이 아니면 null.
export function parseNotificationEvent(data) {
  try {
    const parsed = typeof data === "string" ? JSON.parse(data) : data;
    if (!parsed || !Number.isFinite(Number(parsed.id)) || typeof parsed.title !== "string") return null;
    return parsed;
  } catch (_) {
    return null;
  }
}

// 서버가 보낸 `event: unread` 의 data — { unread, latestId }. 수가 아니면 null(무시).
// latestId 는 keepalive 에도 실려 오므로, 허브가 놓친 알림(다른 프로세스가 쓴 것)을 브라우저가 알아챈다.
export function parseUnreadEvent(data) {
  try {
    const parsed = typeof data === "string" ? JSON.parse(data) : data;
    const count = Number(parsed?.unread);
    if (!Number.isFinite(count) || count < 0) return null;
    const latest = Number(parsed?.latest_id);
    return { unread: Math.floor(count), latestId: Number.isFinite(latest) ? latest : null };
  } catch (_) {
    return null;
  }
}
