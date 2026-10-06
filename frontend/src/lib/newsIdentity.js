// Matches server publisher URL identity. Query article IDs remain intact.
const tracking = new Set(["fbclid", "gclid", "dclid", "msclkid", "yclid", "igshid", "mc_cid", "mc_eid"]);

export function newsIdentity(item) {
  if (item?.content_type === "community") {
    return JSON.stringify(["community", item.source || "Binance Square", item.community_post_id || item.url || item.id]);
  }
  const raw = String(item?.url || "");
  if (raw.length <= 2000 && !/[\s\x00-\x1f\x7f]/.test(raw)) {
    try {
      const url = new URL(raw);
      if (["http:", "https:"].includes(url.protocol)) {
        const query = url.search.slice(1).split("&").filter(token => {
          const key = token.split("=", 1)[0].toLowerCase();
          return token && !key.startsWith("utm_") && !tracking.has(key);
        }).sort().join("&");
        const fragment = url.hash.startsWith("#/") || url.hash.startsWith("#!/") ? url.hash : "";
        return `article-url|https://${url.host.toLowerCase()}${url.pathname.replace(/\/+$/, "")}${query ? `?${query}` : ""}${fragment}`;
      }
    } catch { /* malformed URL falls back to stable item ID */ }
  }
  return item?.id || JSON.stringify([String(item?.original_title || item?.title || "").trim().replace(/\s+/g, " ").toLowerCase(), item?.source || ""]);
}

export function dedupeNewsItems(items, { preferLast = false } = {}) {
  const byIdentity = new Map();
  for (const item of items || []) {
    const key = newsIdentity(item);
    if (preferLast || !byIdentity.has(key)) byIdentity.set(key, item);
  }
  return [...byIdentity.values()];
}
