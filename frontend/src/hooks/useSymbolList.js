import { useSyncExternalStore } from "react";
import { api } from "../api.js";
import { createSharedResource } from "../lib/sharedResource.js";

const symbols = createSharedResource(async (signal) => {
  const data = await api.symbols({ signal });
  return { items: Array.isArray(data?.items) ? data.items : [], fetchedAt: Date.now(), stale: !!data?.stale };
}, { ttlMs: 5 * 60_000, retryMs: 30_000, isStale: (data) => data.stale || !data.items.length });

export const loadSymbolList = (force = false) => symbols.refresh(force);
export function useSymbolList() {
  const state = useSyncExternalStore(symbols.subscribe, symbols.getSnapshot, symbols.getSnapshot);
  return { items: state.data?.items || null, loading: state.loading, error: state.error,
    reload: () => symbols.refresh(true).catch(() => {}) };
}
export const resetSymbolList = () => symbols.reset();
