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
