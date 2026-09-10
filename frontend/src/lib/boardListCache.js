// Short-lived, bounded, account-scoped list cache. Never store post bodies or keys.
export function createBoardListCache({ ttl = 30_000, capacity = 20, now = Date.now } = {}) {
  const entries = new Map();
  const listeners = new Set();
  let version = 0;
  return {
    version: () => version,
    subscribe(listener) { listeners.add(listener); return () => listeners.delete(listener); },
    peek(key) {
      const entry = entries.get(key);
      if (!entry || now() - entry.at >= ttl) { entries.delete(key); return undefined; }
      entries.delete(key); entries.set(key, entry);
      return entry.value;
    },
    async load(key, fetcher) {
      const cached = this.peek(key);
      if (cached !== undefined) return cached;
      const startedVersion = version;
      const value = await fetcher(startedVersion);
      // A mutation may have completed while this older GET was in flight.
      if (version === startedVersion) {
        entries.delete(key);
        entries.set(key, { value, at: now() });
        while (entries.size > capacity) entries.delete(entries.keys().next().value);
      }
      return value;
    },
    updatePost(post) {
      for (const entry of entries.values()) {
        entry.value = { ...entry.value, items: entry.value.items.map(item => item.id === post.id
          ? { ...item, views: post.views, likes: post.likes, dislikes: post.dislikes } : item) };
      }
    },
    invalidate() {
      version += 1;
      entries.clear();
      listeners.forEach(listener => listener());
    },
  };
}
