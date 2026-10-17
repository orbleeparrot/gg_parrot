import { useSyncExternalStore } from "react";
import { api } from "../api.js";
import { createSharedResource } from "../lib/sharedResource.js";
import { newsCache } from "../lib/newsBriefings.js";

// 거래소마다 따로 받는다 — 바이낸스는 예전 캐시 열쇠(hot-coins)를 그대로 쓴다.
const resources = new Map();
export function hotCoinsResource(exchange = "binance") {
  if (!resources.has(exchange)) {
    const key = exchange === "binance" ? "hot-coins" : `hot-coins:${exchange}`;
    resources.set(exchange, createSharedResource((signal) => api.hotCoins(10, { signal }, exchange), {
      ttlMs: Number(import.meta.env?.VITE_HOTCOINS_POLL_MS) || 45_000,
      seed: newsCache.get(key),
      isStale: (data) => !!data.stale || !data.coins?.length,
      onData: (data, at) => newsCache.set(key, data, at),
    }));
  }
  return resources.get(exchange);
}

export default function useHotCoins(exchange = "binance") {
  const resource = hotCoinsResource(exchange);
  const state = useSyncExternalStore(resource.subscribe, resource.getSnapshot, resource.getSnapshot);
  return { coins: state.data?.coins || [], loading: state.loading, error: state.error, refresh: resource.refresh };
}
