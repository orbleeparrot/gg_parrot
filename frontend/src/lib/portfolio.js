// 여러 종목(포트폴리오) 표기 — 카드 제목과 종목별 비중. 백테스트는 자금을 종목 수만큼 균등하게 나눈다.
import { baseOf } from "./format.js";

// 카드 제목: 3개까지는 다 쓰고, 그보다 많으면 앞의 둘 + "외 n종목".
export function portfolioTitle(symbols) {
  const bases = (symbols || []).map((s) => baseOf(String(s || ""))).filter(Boolean);
  if (bases.length <= 1) return bases[0] || "";
  if (bases.length <= 3) return bases.join(" · ");
  return `${bases[0]} · ${bases[1]} 외 ${bases.length - 2}종목`;
}

// 종목 하나의 비중 — "1/3 · 33%".
export function portfolioWeight(count) {
  const n = Math.max(1, Number(count) || 1);
  const pct = 100 / n;
  const shown = Number.isInteger(pct) ? String(pct) : pct.toFixed(pct < 10 ? 1 : 0);
  return { fraction: `1/${n}`, percent: `${shown}%` };
}

// 서버의 summary._base_of 와 같은 규칙으로 티커를 짧게 부른다.
const QUOTES = ["USDT", "KRW", "BUSD"];

export function baseTicker(symbol) {
  const s = String(symbol || "").toUpperCase();
  for (const q of QUOTES) if (s.endsWith(q)) return s.slice(0, -q.length);
  return s;
}

// "BTC 50% · ETH 30%" — 비중이 균등하지 않은 묶음을 한 줄로 보여 준다. 부동소수 찌꺼기는 소수 둘째 자리에서 자른다.
export function weightPhrase(legs) {
  if (!Array.isArray(legs) || legs.length === 0) return "";
  return legs.map((leg) => `${baseTicker(leg.symbol)} ${Number(Number(leg.weight).toFixed(2))}%`).join(" · ");
}

// 서버 bundle.BundleGate.note() 와 같은 문구를 낸다. 한쪽만 고치면 두 화면이 달라진다.
export function bundleLimitPhrase(risk) {
  if (!risk) return "";
  const parts = [];
  if (risk.max_positions != null) parts.push(`한 번에 ${risk.max_positions}종목까지`);
  if (risk.max_exposure_pct != null) parts.push(`총 노출 ${Number(risk.max_exposure_pct)}% 까지`);
  return parts.join(" · ");
}
