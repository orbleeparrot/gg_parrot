// 매크로 파일(.ggm.json) 이름 — 영문으로, 다운로드 폴더와 실행기 매크로 카드 제목에서 서로 구분되게.
// 거래소_종목_전략_방향_날짜(KST).ggm.json  예) bithumb_KRW-BTR_rsi_long_20261013.ggm.json
const STRATEGY_SLUGS = {
  A: "tp-sl-reentry",
  B: "limit-band",
  C: "dca",
  D: "grid",
  E: "trailing-stop",
  F: "rsi",
  G: "bollinger",
  H: "martingale",
  I: "volatility-breakout",
  J: "ma-cross",
  K: "sar-defense",
};
const EXCHANGES = new Set(["binance", "upbit", "bithumb"]);
const KST_MS = 9 * 3_600_000;

function part(value, fallback) {
  // Windows 가 못 쓰는 글자 · 공백 · 한글은 빼고 영문 · 숫자 · '-' 만 남긴다.
  const clean = String(value ?? "").replace(/[^A-Za-z0-9-]+/g, "").replace(/^-+|-+$/g, "");
  return clean || fallback;
}

export function macroFileName(macro, now = new Date()) {
  const m = macro || {};
  const exchange = EXCHANGES.has(String(m.exchange || "").toLowerCase()) ? String(m.exchange).toLowerCase() : "binance";
  const symbols = Array.isArray(m.symbols) ? m.symbols.filter(Boolean) : [];
  const symbol = part(m.symbol || symbols[0], "macro") + (symbols.length > 1 ? `-plus${symbols.length - 1}` : "");
  const strategy = STRATEGY_SLUGS[String(m.rule_type || "").toUpperCase()] || part(String(m.rule_type || "").toLowerCase(), "custom");
  const side = m.position_side === "short" ? "short" : "long";
  const day = new Date(now.getTime() + KST_MS).toISOString().slice(0, 10).replaceAll("-", "");
  return `${exchange}_${symbol}_${strategy}_${side}_${day}.ggm.json`;
}
