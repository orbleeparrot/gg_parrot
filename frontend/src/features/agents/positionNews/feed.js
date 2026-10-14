import { dedupeNewsItems } from "../../../lib/newsIdentity.js";

// A cursor response carries changed articles, including enrichment updates.
export function mergePositionNews(current, incoming) {
  // Tag only this raw delta. Retained earlier articles must never borrow the
  // newest response receipt, which may cover an entirely different batch.
  if (incoming && Object.hasOwn(incoming, "receipt_key") && Array.isArray(incoming.items)) {
    incoming = { ...incoming, items: incoming.items.map((item) => ({ ...item,
      _activity_receipt_key: typeof incoming.receipt_key === "string" ? incoming.receipt_key : null })) };
  }
  if (!Number.isSafeInteger(incoming?.cursor) || incoming.reset || !current) {
    return incoming?.items ? { ...incoming, items: dedupeNewsItems(incoming.items) } : incoming;
  }
  const byId = new Map((current.items || []).map((item) => [item.id, item]));
  for (const item of incoming.items || []) byId.set(item.id, { ...byId.get(item.id), ...item });
  // Changed aliases replace old values without reversing equal-date items on
  // every poll. The server cursor and existing display order stay intact.
  const items = dedupeNewsItems([...byId.values()], { preferLast: true }).sort((a, b) => (
    (Date.parse(b.published || "") || 0) - (Date.parse(a.published || "") || 0)
  )).slice(0, 200);
  return { ...current, ...incoming, items };
}
