// A cursor response carries changed articles, including enrichment updates.
export function mergePositionNews(current, incoming) {
  if (!Number.isSafeInteger(incoming?.cursor) || incoming.reset || !current) return incoming;
  const byId = new Map((current.items || []).map((item) => [item.id, item]));
  for (const item of incoming.items || []) byId.set(item.id, { ...byId.get(item.id), ...item });
  const items = [...byId.values()].sort((a, b) => (
    (Date.parse(b.published || "") || 0) - (Date.parse(a.published || "") || 0)
  )).slice(0, 200);
  return { ...current, ...incoming, items };
}
