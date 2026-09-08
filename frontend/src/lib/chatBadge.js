// 플로팅 채팅의 'N new' 배지 — 마지막으로 본 메시지 id 를 브라우저에 저장해 두고,
// 패널이 닫혀 있는 동안 도착한 메시지 수를 센다. 메시지 id 는 단조 증가한다.
export const CHAT_SEEN_STORAGE_KEY = "ggparrot:chat-seen:v1";

export function latestMessageId(items) {
  let latest = 0;
  for (const item of items || []) {
    const id = Number(item?.id);
    if (Number.isFinite(id) && id > latest) latest = id;
  }
  return latest;
}

export function countUnseen(items, seenId) {
  if (seenId === null || seenId === undefined) return 0; // 저장된 값이 없으면 배지 없음
  const seen = Number(seenId);
  if (!Number.isFinite(seen)) return 0;
  return (items || []).filter((item) => Number(item?.id) > seen).length;
}

export function badgeLabel(count) {
  if (count <= 0) return "";
  return `${count > 99 ? "99+" : count} new`;
}

export function readSeenId() {
  try {
    const raw = window.localStorage.getItem(CHAT_SEEN_STORAGE_KEY);
    if (raw == null) return null;
    const value = Number(raw);
    return Number.isFinite(value) ? value : null;
  } catch (_) {
    return null;
  }
}

export function writeSeenId(id) {
  try {
    window.localStorage.setItem(CHAT_SEEN_STORAGE_KEY, String(id));
  } catch (_) {
    // 시크릿 창·차단된 저장소 — 배지는 이번 방문 안에서만 동작한다.
  }
}
