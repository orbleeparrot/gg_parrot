import { useEffect, useState } from "react";
import { api } from "../api.js";

// 매크로가 판정에 쓰는 봉 간격의 최근 캔들 — 진입 대기 카드 · 시작 전 안내가 함께 쓴다.
// 차트는 사용자가 간격을 바꿀 수 있어 그 데이터를 빌리지 않는다. 1분마다(그리고 봉이 바뀔 때) 다시 받는다.
export default function useMacroCandles({ symbol, interval, market = "spot", exchange = "binance", enabled = true, limit = 300 }) {
  const [candles, setCandles] = useState([]);
  useEffect(() => {
    if (!enabled || !symbol || !interval) {
      setCandles([]);
      return undefined;
    }
    let alive = true;
    const load = async () => {
      try {
        const data = await api.candles(symbol, interval, limit, market, exchange);
        if (alive) setCandles(Array.isArray(data?.candles) ? data.candles : []);
      } catch {
        /* 다음 주기에 다시 받는다 — 카드는 받은 값이 없으면 문장만 보여 준다 */
      }
    };
    void load();
    const timer = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(timer); };
  }, [symbol, interval, market, exchange, enabled, limit]);
  return candles;
}
