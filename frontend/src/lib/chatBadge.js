// Account-scoped cursors. The old shared v1 value cannot identify its reader.
export const CHAT_SEEN_STORAGE_KEY = "ggparrot:chat-seen:v2";

export function chatScope(userId) {
  return userId == null ? "anon" : `member:${userId}`;
}

export function seenStorageKey(scope = "anon") {
  return `${CHAT_SEEN_STORAGE_KEY}:${scope}`;
}

export function latestMessageId(items) {
  return (items || []).reduce((latest, item) => Math.max(latest, Number(item?.id) || 0), 0);
}

export function isOwnMessage(message, userId) {
  return userId != null && message?.user_id != null && String(message.user_id) === String(userId);
}

export function sameAuthor(left, right) {
  return left?.user_id != null && right?.user_id != null && String(left.user_id) === String(right.user_id);
}

export function countUnseen(items, seenId, userId = null) {
  if (seenId == null || !Number.isFinite(Number(seenId))) return 0;
  return (items || []).filter((item) => Number(item?.id) > Number(seenId) && !isOwnMessage(item, userId)).length;
}

export function firstUnseenId(items, seenId, userId = null) {
  if (seenId == null || !Number.isFinite(Number(seenId))) return null;
  const ids = (items || []).filter((item) => Number(item?.id) > Number(seenId) && !isOwnMessage(item, userId)).map((item) => Number(item.id));
  return ids.length ? Math.min(...ids) : null;
}

export function badgeLabel(count) {
  return count > 0 ? `${count > 99 ? "99+" : count} new` : "";
}

export function readSeenId(scope = "anon") {
  try {
    const raw = window.localStorage.getItem(seenStorageKey(scope));
    const value = raw == null ? null : Number(raw);
    return value != null && Number.isSafeInteger(value) && value >= 0 ? value : null;
  } catch {
    return null;
  }
}

export function writeSeenId(id, scope = "anon") {
  if (!Number.isSafeInteger(Number(id)) || Number(id) < 0) return readSeenId(scope);
  const next = Math.max(Number(id), readSeenId(scope) ?? 0);
  try {
    window.localStorage.setItem(seenStorageKey(scope), String(next));
  } catch {
    // The in-memory account cache still works when storage is unavailable.
  }
  return next;
}
