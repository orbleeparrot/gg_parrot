import { useSyncExternalStore } from "react";
import { api } from "../api.js";
import { createSharedResource } from "../lib/sharedResource.js";
import { newsCache } from "../lib/newsBriefings.js";

const KEY = "hot-coins";
export const hotCoinsResource = createSharedResource((signal) => api.hotCoins(10, { signal }), {
  ttlMs: Number(import.meta.env?.VITE_HOTCOINS_POLL_MS) || 45_000,
  seed: newsCache.get(KEY),
  isStale: (data) => !!data.stale || !data.coins?.length,
  onData: (data, at) => newsCache.set(KEY, data, at),
});
export default function useHotCoins() {
  const state = useSyncExternalStore(hotCoinsResource.subscribe, hotCoinsResource.getSnapshot, hotCoinsResource.getSnapshot);
  return { coins: state.data?.coins || [], loading: state.loading, error: state.error, refresh: hotCoinsResource.refresh };
}
