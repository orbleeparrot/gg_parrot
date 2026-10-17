import { useSyncExternalStore } from "react";

// 코인동향에서 고른 거래소 — 다시 들어와도 이어지게 브라우저에 기억한다(못 읽으면 기본값 업비트).
// 하단 '오늘의 경주마' 띠는 2026-10-17 부터 이 값과 상관없이 거래소를 차례로 돌린다(lib/marqueeRotation.js).
const KEY = "ggp_trends_exchange";
export const TRENDS_EXCHANGES = ["upbit", "bithumb", "binance"];
const DEFAULT = "upbit";

function read() {
  try {
    const saved = globalThis.localStorage?.getItem(KEY);
    return TRENDS_EXCHANGES.includes(saved) ? saved : DEFAULT;
  } catch {
    return DEFAULT;
  }
}

let current = null;
const listeners = new Set();

export function getTrendsExchange() {
  if (current == null) current = read();
  return current;
}

export function setTrendsExchange(exchange) {
  if (!TRENDS_EXCHANGES.includes(exchange) || exchange === getTrendsExchange()) return;
  current = exchange;
  try { globalThis.localStorage?.setItem(KEY, exchange); } catch { /* 기억 못 해도 이번 방문 동안은 따라간다 */ }
  listeners.forEach((listener) => listener());
}

function subscribe(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTrendsExchange() {
  return useSyncExternalStore(subscribe, getTrendsExchange, getTrendsExchange);
}
