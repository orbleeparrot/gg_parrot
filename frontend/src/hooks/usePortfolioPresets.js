// 추천 묶음 — 거래소·시장마다 서버가 기준을 풀어 준 결과를 받아 둔다.
//
// 종목 목록(useSymbolList)과 달리 이건 **없어도 되는 것**이다. 못 받으면 조용히 빈 목록을
// 돌려주고 화면은 묶음 줄만 안 그린다 — 종목 검색은 그대로 쓸 수 있어야 한다.
//
// 한 번 받은 것은 모듈에 들고 있다. 빌더에서 거래소를 왔다 갔다 할 때마다 다시 받으면
// 서버의 코인게코 호출만 늘어난다(무료 등급이라 분당 호출이 빡빡하다).
import { useEffect, useState } from "react";
import { api } from "../api.js";

const cache = new Map();
const inflight = new Map();

function keyOf(exchange, market) {
  return `${exchange}|${market === "futures" ? "futures" : "spot"}`;
}

function load(exchange, market) {
  const key = keyOf(exchange, market);
  if (cache.has(key)) return Promise.resolve(cache.get(key));
  if (inflight.has(key)) return inflight.get(key);
  const [ex, mk] = key.split("|");
  const promise = api.portfolios({ exchange: ex, market: mk })
    .then((body) => {
      const items = Array.isArray(body?.items) ? body.items : [];
      cache.set(key, items);
      return items;
    })
    .catch(() => [])          // 없어도 되는 것 — 화면을 막지 않는다
    .finally(() => inflight.delete(key));
  inflight.set(key, promise);
  return promise;
}

// ``ready`` 는 **한 번이라도 답을 받았는지**다. 이걸 안 주면 받는 동안의 빈 목록과 정말
// 못 받은 빈 목록이 같아 보여서, 화면에 "불러오지 못했어요" 가 한 번 번쩍이고 사라진다.
export function usePortfolioPresets(exchange = "binance", market = "spot") {
  const key = keyOf(exchange, market);
  const [state, setState] = useState(() => ({ items: cache.get(key) || [], ready: cache.has(key) }));
  useEffect(() => {
    let alive = true;
    // 이미 받아 둔 것이 있으면 그걸로 먼저 그린다 — 거래소를 바꿀 때 줄이 깜빡이지 않게.
    setState({ items: cache.get(key) || [], ready: cache.has(key) });
    load(exchange, market).then((rows) => { if (alive) setState({ items: rows, ready: true }); });
    return () => { alive = false; };
  }, [exchange, key, market]);
  return state;
}

export const resetPortfolioPresets = () => { cache.clear(); inflight.clear(); };
