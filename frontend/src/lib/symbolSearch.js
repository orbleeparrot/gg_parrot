// 종목 검색 — 서버의 실제 거래 가능 목록(/api/symbols) 안에서만 고른다.
// 리더보드는 티커를 `CHIP` 처럼 보여 주므로 base 로도, `CHIPUSDT` 전체로도 찾는다.

const clean = (text) => String(text || "").trim().toUpperCase().replace(/[^A-Z0-9]/g, "");

// 검색어에 맞는 종목을 관련도 순으로 — base 일치 > base 앞머리 > 심볼 앞머리 > 포함.
export function searchSymbols(items, query, { limit = 8, exclude = [] } = {}) {
  const q = clean(query);
  if (!q || !Array.isArray(items)) return [];
  const skip = new Set(exclude.map(clean));
  const ranked = [];
  for (const item of items) {
    if (skip.has(item.symbol)) continue;
    const base = String(item.base || "").toUpperCase();
    const symbol = String(item.symbol || "").toUpperCase();
    let score = -1;
    if (base === q || symbol === q) score = 0;
    else if (base.startsWith(q)) score = 1;
    else if (symbol.startsWith(q)) score = 2;
    else if (base.includes(q) || symbol.includes(q)) score = 3;
    if (score < 0) continue;
    ranked.push({ item, score, len: base.length });
  }
  ranked.sort((a, b) => a.score - b.score || a.len - b.len || a.item.symbol.localeCompare(b.item.symbol));
  return ranked.slice(0, limit).map((entry) => entry.item);
}

// 입력한 글을 실제 심볼로 — `btc` · `BTC` · `BTCUSDT` 모두 BTCUSDT. 목록에 없으면 null.
export function resolveSymbol(items, text) {
  const q = clean(text);
  if (!q || !Array.isArray(items)) return null;
  const bySymbol = new Map(items.map((item) => [String(item.symbol).toUpperCase(), item]));
  if (bySymbol.has(q)) return bySymbol.get(q).symbol;
  if (bySymbol.has(`${q}USDT`)) return bySymbol.get(`${q}USDT`).symbol;
  const byBase = items.find((item) => String(item.base || "").toUpperCase() === q);
  return byBase ? byBase.symbol : null;
}

// 시장 표시 — 현물·선물 둘 다면 둘 다, 하나뿐이면 그것만.
export function marketTags(item) {
  const tags = [];
  if (item?.spot) tags.push("현물");
  if (item?.futures) tags.push("선물");
  return tags;
}
