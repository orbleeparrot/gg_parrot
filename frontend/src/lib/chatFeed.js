// Merge snapshots by ID: an older GET must not remove a successful POST or a
// previously loaded history page. Only the server's KST day boundary prunes rows.
export function mergeMessages(items, incoming, dayStartMs = 0) {
  const previous = items || [];
  const messages = new Map();
  let changed = false;
  let orderChanged = false;
  let highestId = 0;
  for (const item of previous) {
    if (item?.id == null) continue;
    const created = Number(item.created_ms) || Date.parse(item.created_at);
    if (dayStartMs && Number.isFinite(created) && created < dayStartMs) { changed = true; continue; }
    messages.set(Number(item.id), item);
    highestId = Math.max(highestId, Number(item.id));
  }
  for (const item of incoming || []) {
    if (item?.id == null) continue;
    const created = Number(item.created_ms) || Date.parse(item.created_at);
    if (dayStartMs && Number.isFinite(created) && created < dayStartMs) continue;
    const existing = messages.get(Number(item.id));
    if (existing && JSON.stringify(existing) === JSON.stringify(item)) continue;
    changed = true;
    if (!existing && Number(item.id) < highestId) orderChanged = true;
    highestId = Math.max(highestId, Number(item.id));
    messages.set(Number(item.id), item);
  }
  if (!changed) return previous;
  const result = [...messages.values()];
  return orderChanged ? result.sort((a, b) => Number(a.id) - Number(b.id)) : result;
}

export const CHAT_WINDOW_SIZE = 200;

// A fixed window bounds DOM work while retaining every fetched message in the
// cache. Its upper ID stays stable when older pages or live arrivals are merged.
export function chatWindow(items, endId = null, size = CHAT_WINDOW_SIZE) {
  const end = endId == null ? items.length : items.findIndex((item) => Number(item.id) > endId);
  const finish = end < 0 ? items.length : end;
  const start = Math.max(0, finish - size);
  return { items: items.slice(start, finish), start, end: finish, hasOlder: start > 0, hasNewer: finish < items.length };
}

export function appendMessage(items, message) {
  if (!message || message.id == null) return items;
  const list = Array.isArray(items) ? items : [];
  if (list.some((item) => Number(item.id) === Number(message.id))) return list;
  return mergeMessages(list, [message]);
}
