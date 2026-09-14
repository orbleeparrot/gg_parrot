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
