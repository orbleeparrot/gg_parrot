import { useSyncExternalStore } from "react";
import { api } from "../api.js";
import { createSymbolListResource, symbolListView } from "../lib/symbolListResource.js";
import { normalizeExchange } from "../lib/exchanges.js";

const resources = new Map();
function symbolsFor(value) {
  const exchange = normalizeExchange(value);
  if (!resources.has(exchange)) resources.set(exchange, createSymbolListResource(exchange, (options) => api.symbols(options)));
  return resources.get(exchange);
}

export const loadSymbolList = (force = false, exchange = "binance") => symbolsFor(exchange).refresh(force);
export function useSymbolList(exchange = "binance") {
  const symbols = symbolsFor(exchange);
  const state = useSyncExternalStore(symbols.subscribe, symbols.getSnapshot, symbols.getSnapshot);
  return { ...symbolListView(state, exchange),
    canChoose: (symbol) => !!symbolListView(symbols.getSnapshot(), exchange).items?.some((item) => item.symbol === symbol),
    reload: () => symbols.refresh(true).catch(() => {}) };
}
export const resetSymbolList = () => resources.forEach((symbols) => symbols.reset());
