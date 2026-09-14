// 거래 가능한 종목 목록(/api/symbols) — 한 번 받아 모듈에 두고 모든 조건 판이 같이 쓴다.
import { useEffect, useState } from "react";
import { api } from "../api.js";

let cache = null;          // { items, fetchedAt }
let inflight = null;       // Promise
const listeners = new Set();

function notify() { for (const fn of listeners) fn(); }

export function loadSymbolList(force = false) {
  if (cache && !force) return Promise.resolve(cache);
  if (inflight) return inflight;
  inflight = api.symbols()
    .then((data) => {
      cache = { items: Array.isArray(data?.items) ? data.items : [], fetchedAt: Date.now(), stale: !!data?.stale };
      return cache;
    })
    .finally(() => { inflight = null; notify(); });
  notify();
  return inflight;
}

export function useSymbolList() {
  const [, bump] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    const onChange = () => bump((n) => n + 1);
    listeners.add(onChange);
    if (!cache) loadSymbolList().catch((e) => setError(String(e?.message || e)));
    return () => listeners.delete(onChange);
  }, []);
  const reload = () => { setError(""); return loadSymbolList(true).catch((e) => setError(String(e?.message || e))); };
  return { items: cache?.items || null, loading: !cache && !!inflight, error, reload };
}

// 테스트·미리보기용 초기화
export function resetSymbolList() { cache = null; inflight = null; }
