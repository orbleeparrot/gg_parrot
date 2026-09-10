import { useCallback, useEffect, useRef, useState } from "react";
import { cachedNewsState, createNewsBriefingQueue, newsCache } from "../lib/newsBriefings.js";

// 뉴스 브리핑 큐 — 화면을 떠났다 돌아오면 공유 캐시(`newsCache`)의 마지막 응답을 먼저 그리고,
// `freshMs` 안이면 요청을 내지 않는다. 그보다 오래됐으면 보이는 채로 조용히 새로 받는다.
export default function useNewsBriefings(keys, load, concurrency = 2, { cache = newsCache, freshMs = 5 * 60 * 1000 } = {}) {
  const keysKey = JSON.stringify(keys);
  const [states, setStates] = useState(() => Object.fromEntries(keys.map((key) => [key, cachedNewsState(cache, key)])));
  const queueRef = useRef(null);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const requested = JSON.parse(keysKey);
    setStates(Object.fromEntries(requested.map((key) => [key, cachedNewsState(cache, key)])));
    const queue = createNewsBriefingQueue({
      keys: requested,
      load: (key, signal) => loadRef.current(key, signal),
      concurrency,
      cache,
      freshMs,
      onChange: (key, state) => setStates((current) => ({ ...current, [key]: state })),
    });
    queueRef.current = queue;
    const syncVisibility = () => queue.setVisible(!document.hidden);
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    queue.start();
    return () => {
      document.removeEventListener("visibilitychange", syncVisibility);
      queue.stop();
      if (queueRef.current === queue) queueRef.current = null;
    };
  }, [keysKey, concurrency, cache, freshMs]);

  const retry = useCallback((key) => queueRef.current?.retry(key), []);
  return { states, retry };
}
