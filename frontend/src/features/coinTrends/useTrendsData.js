import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "../../api.js";
import useAdaptivePolling from "../../hooks/useAdaptivePolling.js";
import { createSharedResource } from "../../lib/sharedResource.js";
import { mergeById, refreshFirstPage } from "./trendsFormat.js";

// 시세 순위 — 거래소마다 한 번에 받는다(업비트 300·빗썸 500개 안팎, 서버 시세 캐시 30초).
// 탭 정렬 · 검색 · 코인 정보 · 앞뒤 순위가 모두 이 한 목록에서 나온다. 그리기는 화면이 나눠서 한다.
const rankingResources = new Map();
function rankingsResource(exchange) {
  if (!rankingResources.has(exchange)) {
    rankingResources.set(exchange, createSharedResource(
      (signal) => api.marketTrends(exchange, { signal }),
      { ttlMs: 30_000, isStale: (data) => !!data?.stale || !data?.coins?.length },
    ));
  }
  return rankingResources.get(exchange);
}

const IDLE = { data: null, loading: false, error: "" };
const subscribeNothing = () => () => {};
const idleSnapshot = () => IDLE;

// enabled=false 면 구독하지 않는다 — 다른 거래소 시세는 검색하거나 코인을 고를 때만 받는다.
export function useRankings(exchange, enabled = true) {
  const resource = enabled && exchange ? rankingsResource(exchange) : null;
  return useSyncExternalStore(resource ? resource.subscribe : subscribeNothing,
    resource ? resource.getSnapshot : idleSnapshot, resource ? resource.getSnapshot : idleSnapshot);
}

const usdKrwResource = createSharedResource((signal) => api.usdKrw({ signal }), { ttlMs: 10 * 60_000 });
// 환율 — 조회에 실패해 서버가 고정 대체값을 주면(is_fallback) '해외 대비'를 그리지 않는다(몇 %씩 틀릴 수 있다).
export function useUsdKrw(enabled) {
  const state = useSyncExternalStore(enabled ? usdKrwResource.subscribe : subscribeNothing,
    enabled ? usdKrwResource.getSnapshot : idleSnapshot, enabled ? usdKrwResource.getSnapshot : idleSnapshot);
  const rate = Number(state.data?.usdkrw);
  return Number.isFinite(rate) && rate > 0 && !state.data?.is_fallback ? rate : null;
}

export const FEED_PAGE = 20;
// 다음 쪽 요청이 연달아 이만큼 실패하면 자동으로 다시 부르지 않는다(첫 쪽 새로고침이 성공하면 다시 연다).
const MAX_PAGE_FAILURES = 3;

const blank = (key) => ({ key, items: [], total: 0, nextCursor: null, failures: 0, extra: null, loading: true, error: "", more: false });

// 소식 목록 하나(커뮤니티 또는 뉴스). 서버가 DB 에서 한 쪽(20개)만 읽어 준다(2026-10-18).
// 첫 쪽은 60초마다 조용히 다시 받아 위에 붙이고, 목록 끝에 닿으면 next_cursor(마지막으로 받은 글)로 다음 쪽을 받는다 —
// 커서는 마지막 글 자체라 머리에 새 글이 쌓여도 다음 쪽이 밀리거나 겹치지 않는다.
// enabled=false 면 받지 않는다 — 거래소 공지는 지금 빗썸만 있어 다른 거래소에선 부르지 않는다.
export function useFeedList(kind, { region, exchange, symbol = "", q = "", source = "", enabled = true }) {
  const key = [kind, region, exchange, symbol, q, source].join("|");
  const [state, setState] = useState(() => blank(key));
  const keyRef = useRef(key);
  keyRef.current = key;
  const params = useCallback((cursor) => ({
    region, exchange, sections: kind, limit: FEED_PAGE,
    ...(cursor ? { cursor } : {}),
    ...(symbol ? { symbol } : {}), ...(q ? { q } : {}), ...(source ? { source } : {}),
  }), [kind, region, exchange, symbol, q, source]);

  useEffect(() => { setState(blank(key)); }, [key]);

  const loadFirst = useCallback(async (signal) => {
    const requestKey = key;
    try {
      const data = await api.trendsFeed(params(null), { signal });
      if (keyRef.current !== requestKey) return;
      const section = data?.[kind] || {};
      const page = section.items || [];
      setState((current) => {
        const same = current.key === requestKey;
        const merged = refreshFirstPage(same ? current.items : [], page);
        // 겹치면 새 글만 위에 붙이고 이어 받을 자리는 그대로, 안 겹치면 첫 쪽으로 갈아 끼우고 서버의 다음 자리부터.
        const nextCursor = merged.replaced ? section.next_cursor || null : current.nextCursor;
        return { key: requestKey, items: merged.items, total: Number(section.total) || 0, nextCursor, failures: 0,
          extra: { ...section, sources: data?.sources || [] }, loading: false, error: "", more: same && current.more };
      });
    } catch (reason) {
      if (keyRef.current !== requestKey || reason?.name === "AbortError") return;
      setState((current) => ({ ...current, loading: false, error: reason?.message || String(reason) }));
      throw reason;
    }
  }, [key, kind, params]);

  useAdaptivePolling(loadFirst, { intervalMs: 60_000, maxIntervalMs: 5 * 60_000, pollKey: key, enabled });

  const stateRef = useRef(state);
  stateRef.current = state;
  const busyRef = useRef("");
  const loadMore = useCallback(async () => {
    const requestKey = key, current = stateRef.current, cursor = current.nextCursor;
    if (busyRef.current === requestKey || current.key !== requestKey || !current.items.length
      || !cursor || current.failures >= MAX_PAGE_FAILURES) return;
    busyRef.current = requestKey;
    setState((now) => (now.key !== requestKey ? now : { ...now, more: true }));
    try {
      const data = await api.trendsFeed(params(cursor));
      if (keyRef.current !== requestKey) return;
      const section = data?.[kind] || {};
      // 그사이 새로고침이 목록을 갈아 끼웠으면(자리가 바뀜) 이 쪽은 버린다.
      setState((now) => (now.key !== requestKey || now.nextCursor !== cursor ? now : {
        ...now, items: mergeById(now.items, section.items || []), failures: 0, nextCursor: section.next_cursor || null,
      }));
    } catch {
      setState((now) => (now.key !== requestKey ? now : { ...now, failures: now.failures + 1 }));
    } finally {
      if (busyRef.current === requestKey) busyRef.current = "";
      setState((now) => (now.key !== requestKey ? now : { ...now, more: false }));
    }
  }, [key, kind, params]);

  // 열쇠가 바뀐 직후 한 번은 옛 목록을 그리지 않는다.
  const view = state.key === key ? state : blank(key);
  return { ...view, hasMore: !!view.nextCursor && view.failures < MAX_PAGE_FAILURES, loadMore };
}
