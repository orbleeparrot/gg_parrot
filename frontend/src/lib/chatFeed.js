// Merge snapshots by ID: an older GET must not remove a successful POST or a
// previously loaded history page. Only the server's KST day boundary prunes rows.
export function mergeMessages(items, incoming, dayStartMs = 0) {
  const messages = new Map();
  for (const item of [...(items || []), ...(incoming || [])]) {
    if (item?.id == null) continue;
    const created = Number(item.created_ms) || Date.parse(item.created_at);
    if (dayStartMs && Number.isFinite(created) && created < dayStartMs) continue;
    messages.set(Number(item.id), item);
  }
  return [...messages.values()].sort((a, b) => Number(a.id) - Number(b.id));
}

export function appendMessage(items, message) {
  if (!message || message.id == null) return items;
  const list = Array.isArray(items) ? items : [];
  if (list.some((item) => Number(item.id) === Number(message.id))) return list;
  return mergeMessages(list, [message]);
}
