import { createSharedResource } from "./sharedResource.js";
import { isDomestic, normalizeExchange } from "./exchanges.js";

const DOMESTIC_TTL_MS = 60_000;
const DOMESTIC_MAX_AGE_MS = 5 * 60_000;

export function createSymbolListResource(value, load, options = {}) {
  const exchange = normalizeExchange(value);
  const domestic = isDomestic(exchange);
  const now = options.now || Date.now;
  return createSharedResource(async (signal) => {
    const data = await load({ exchange, signal, cache: "no-cache" });
    if (data?.exchange && data.exchange !== exchange) throw new Error("다른 거래소의 종목 목록이 반환됐어요. 다시 확인해 주세요.");
    if (!Array.isArray(data?.items) || !data.items.length) throw new Error("종목 목록이 비어 있어요. 잠시 후 다시 확인해 주세요.");
    const fetched = Number(data?.fetched_at);
    const validTime = data?.fetched_at != null && Number.isFinite(fetched) && fetched > 0;
    if (domestic && !validTime) throw new Error("종목 목록의 확인 시각을 확인하지 못했어요. 다시 확인해 주세요.");
    const fetchedAt = validTime
      ? fetched * (fetched < 100_000_000_000 ? 1_000 : 1) : now();
    return { items: data.items, fetchedAt, stale: !!data?.stale };
  }, { ttlMs: domestic ? DOMESTIC_TTL_MS : 5 * 60_000, retryMs: domestic ? 5_000 : 30_000,
    ...options, isStale: (data) => data.stale || !data.items.length });
}

export function symbolListView(state, value, now = Date.now()) {
  const domestic = isDomestic(value);
  const age = state.data ? Math.max(0, now - state.data.fetchedAt) : 0;
  const expired = domestic && !!state.data && age >= DOMESTIC_MAX_AGE_MS;
  return {
    items: expired ? null : state.data?.items || null,
    loading: state.loading,
    error: state.error || (expired ? "종목 목록이 오래되어 다시 확인하고 있어요." : ""),
    stale: !!state.data && (!!state.error || !!state.data.stale || expired || (domestic && age >= DOMESTIC_TTL_MS)),
    fetchedAt: state.data?.fetchedAt || null,
  };
}
