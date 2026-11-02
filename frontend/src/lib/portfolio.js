// 여러 종목(묶음 · 포트폴리오) 표기 — 카드 제목과 종목별 비중. 비중을 안 정한 묶음(symbols 형태)은
// 자금을 종목 수만큼 똑같이 나누고, 비중을 정한 묶음(legs 형태)은 그 비중대로 나눈다.
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

// "BTC 50% · ETH 30%" — 비중이 균등하지 않은 묶음을 한 줄로 보여 준다.
// 티커 축약은 기존 `baseOf`(format.js)를 쓴다 — 서버 `summary._coin` 과 같은 규칙이고,
// 국내 `KRW-BTC` 접두사도 떼므로 카드와 서버 요약이 어긋나지 않는다. 부동소수 찌꺼기는 소수 둘째 자리에서 자른다.
export function weightPhrase(legs) {
  if (!Array.isArray(legs) || legs.length === 0) return "";
  return legs.map((leg) => `${baseOf(leg.symbol)} ${Number(Number(leg.weight).toFixed(2))}%`).join(" · ");
}

// 서버 bundle.BundleGate.note() 와 같은 문구를 낸다. 한쪽만 고치면 두 화면이 달라진다.
export function bundleLimitPhrase(risk) {
  if (!risk) return "";
  const parts = [];
  if (risk.max_positions != null) parts.push(`한 번에 ${risk.max_positions}종목까지`);
  if (risk.max_exposure_pct != null) parts.push(`총 노출 ${Number(risk.max_exposure_pct)}% 까지`);
  return parts.join(" · ");
}

// 매크로의 종목 목록 — legs 형태와 symbols 형태를 한 자리에서 읽는다.
// 소비처마다 macro.symbols 를 직접 보면 비중 묶음에서 단일 종목으로 그려진다.
export function macroSymbols(macro) {
  if (Array.isArray(macro?.legs) && macro.legs.length > 1) return macro.legs.map((l) => l.symbol);
  if (Array.isArray(macro?.symbols) && macro.symbols.length > 1) return macro.symbols;
  return macro?.symbol ? [macro.symbol] : [];
}

// 서버 summary 의 판정과 같은 기준(max-min < 0.02). 한쪽만 고치면 카드와 요약이 갈린다.
// 서버 `engine/summary.py` 는 `max(weights) - min(weights) < 0.02` 다 — 경계(0.02)는 '균등 아님' 이다.
export function isEvenWeights(legs) {
  if (!Array.isArray(legs) || legs.length < 2) return true;
  const w = legs.map((l) => Number(l.weight));
  if (w.some((n) => !Number.isFinite(n))) return false;
  return Math.max(...w) - Math.min(...w) < 0.02;
}

// 묶음을 두 줄로 가른다 — 분야 묶음(kind="sector")과 기준 묶음(크기 · 거래대금).
// kind 를 모르는 옛 응답은 **기준 쪽**에 둔다. 분야 줄에 올리면 분야가 아닌 것이
// 분야 자리에 앉는다 — 모를 때는 원래 자리가 맞다.
export function splitBundles(items) {
  const rows = Array.isArray(items) ? items : [];
  return {
    sectors: rows.filter((item) => item?.kind === "sector"),
    sizes: rows.filter((item) => item?.kind !== "sector"),
  };
}

// 묶음에 붙는 숫자 두 칸. 서버가 한 레그라도 못 구했으면 `null` 이고, 화면은 칸을 안 그린다.
// 0 을 적으면 화면이 그걸 '거래가 없다' 로 읽는다.
export function bundleStatPhrases(stats) {
  if (!stats) return null;
  const raw = [stats.rank_min, stats.rank_max, stats.range_avg_pct];
  // `Number(null)` 과 `Number("")` 은 0 이다 — 빈 칸을 숫자로 읽으면 '1위' 가 지어진다.
  if (raw.some((v) => v === null || v === undefined || v === "")) return null;
  const [low, high, avg] = raw.map(Number);
  if (![low, high, avg].every((n) => Number.isFinite(n))) return null;
  return { rank: low === high ? `${low}위` : `${low}~${high}위`, range: `${avg.toFixed(1)}%` };
}
