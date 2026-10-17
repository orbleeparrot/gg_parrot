// 코인동향 — 순위 정렬 · 검색 · 숫자 서식. 화면 밖에서 시험하는 순수 함수만 둔다.

export const EXCHANGE_META = {
  upbit: { name: "업비트", region: "kr", quote: "KRW", market: "원화 마켓", changeLabel: "전일 대비", rangeLabel: "오늘" },
  bithumb: { name: "빗썸", region: "kr", quote: "KRW", market: "원화 마켓", changeLabel: "전일 대비", rangeLabel: "오늘" },
  // 바이낸스 고가 · 저가는 지난 24시간 기준(rolling) — '오늘'이라 쓰지 않는다.
  binance: { name: "바이낸스", region: "global", quote: "USDT", market: "USDT 마켓", changeLabel: "24시간", rangeLabel: "24시간" },
};
export const REGION_EXCHANGES = { kr: ["upbit", "bithumb"], global: ["binance"] };
export const EXCHANGES = Object.keys(EXCHANGE_META);

export const regionOf = (exchange) => EXCHANGE_META[exchange]?.region || "kr";
export const coinName = (coin) => coin?.korean_name || coin?.english_name || coin?.base || "";

const number = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

// 원화는 거래소 호가 단위처럼(100원 이상 정수, 그 아래는 자릿수를 늘린다), USDT 는 유효 숫자 기준.
export function formatPrice(value, quote) {
  const p = number(value);
  if (p == null) return "—";
  if (quote === "KRW") {
    const digits = p >= 100 ? 0 : p >= 10 ? 2 : p >= 1 ? 3 : p >= 0.1 ? 4 : 5;
    return p.toLocaleString("ko-KR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  if (p >= 1000) return p.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  // 유효 숫자 4자리 안팎, 지수 표기(6.1e-7) 없이 — 아주 싼 코인(BTTC 등)도 0.000000612 처럼.
  const digits = p >= 1 ? 4 : Math.min(12, Math.max(4, 3 - Math.floor(Math.log10(p))));
  return p.toFixed(digits).replace(/(\.\d*?)0+$/, "$1").replace(/\.$/, "");
}

export function formatTurnover(value, quote) {
  const t = number(value);
  if (t == null) return "—";
  if (quote === "KRW") {
    if (t >= 1e12) return `${(t / 1e12).toFixed(2)}조`;
    if (t >= 1e9) return `${Math.round(t / 1e8).toLocaleString("ko-KR")}억`;
    if (t >= 1e8) return `${(t / 1e8).toFixed(1)}억`;
    if (t >= 1e4) return `${Math.round(t / 1e4).toLocaleString("ko-KR")}만`;
    return `${Math.round(t).toLocaleString("ko-KR")}원`;
  }
  if (t >= 1e9) return `$${(t / 1e9).toFixed(2)}B`;
  if (t >= 1e6) return `$${(t / 1e6).toFixed(1)}M`;
  if (t >= 1e3) return `$${(t / 1e3).toFixed(1)}K`;
  return `$${Math.round(t)}`;
}

export function formatPct(value) {
  const c = number(value);
  if (c == null) return "—";
  return `${c > 0 ? "+" : c < 0 ? "-" : ""}${Math.abs(c).toFixed(2)}%`;
}

export const changeTone = (value) => (number(value) == null || value === 0 ? "flat" : value > 0 ? "up" : "down");

// 탭별 순서. 언급 탭은 말한 글이 있는 코인만 남긴다(0 건이 줄줄이 이어지지 않게).
export function sortCoins(coins, sort, mentions = {}) {
  const list = [...(coins || [])];
  const byTurnover = (a, b) => b.quote_volume - a.quote_volume || a.symbol.localeCompare(b.symbol);
  if (sort === "gainers") return list.sort((a, b) => b.change_pct - a.change_pct || byTurnover(a, b));
  if (sort === "losers") return list.sort((a, b) => a.change_pct - b.change_pct || byTurnover(a, b));
  if (sort === "mentions") {
    return list.filter((coin) => mentions[coin.base] > 0)
      .sort((a, b) => mentions[b.base] - mentions[a.base] || byTurnover(a, b));
  }
  return list.sort(byTurnover);
}

// 경주마 — 서버가 고른 순서(racers) 그대로, 막대 길이는 선두 대비 비율(0~1).
export function raceRows(coins, racers) {
  const byBase = new Map((coins || []).map((coin) => [coin.base, coin]));
  const rows = (racers || []).map((base) => byBase.get(base)).filter((coin) => coin && coin.change_pct > 0);
  const lead = rows[0]?.change_pct || 0;
  return rows.map((coin) => ({ coin, pos: lead > 0 ? Math.max(0, Math.min(1, coin.change_pct / lead)) : 0 }));
}

const squash = (value) => String(value || "").toLowerCase().replace(/\s+/g, "");

// 낮을수록 잘 맞는다. 괄호 속 별칭('엑스알피(리플)'의 리플)은 이름과 같게 본다.
// 영문 한두 글자는 티커와 정확히 같을 때만 — 'T' 가 모든 이름에 걸리지 않게(서버 _matches 와 같은 규칙).
export function matchScore(coin, query) {
  const q = squash(query);
  if (!q) return -1;
  const base = squash(coin.base), ko = squash(coin.korean_name), en = squash(coin.english_name);
  const aliases = String(coin.korean_name || "").split(/[()[\]]/).map(squash).filter(Boolean);
  if (base === q) return 0;
  if (ko === q || aliases.includes(q)) return 1;
  if (/^[a-z0-9]{1,2}$/.test(q)) return -1;
  if (base.startsWith(q)) return 2;
  if (ko.startsWith(q)) return 3;
  if (ko.includes(q)) return 4;
  if (en.startsWith(q)) return 5;
  if (en.includes(q)) return 6;
  return -1;
}

// 지금 거래소에서 맞는 코인(최대 here 개) + 다른 거래소에만 있는 코인(최대 other 개).
export function searchCoins(query, { exchange, lists }, { here = 6, other = 2 } = {}) {
  const rank = (coins) => (coins || []).map((coin) => [matchScore(coin, query), coin])
    .filter(([score]) => score >= 0)
    .sort((a, b) => a[0] - b[0] || b[1].quote_volume - a[1].quote_volume)
    .map(([, coin]) => coin);
  const current = rank(lists?.[exchange]);
  const seen = new Set((lists?.[exchange] || []).map((coin) => coin.base));
  // 다른 거래소 결과는 합친 뒤 점수로 줄 세운다 — 빗썸의 약한 영문명 일치가 바이낸스의 정확한 티커를 가리지 않게.
  const elsewhere = [];
  EXCHANGES.filter((name) => name !== exchange).forEach((name, order) => {
    for (const coin of lists?.[name] || []) {
      const score = matchScore(coin, query);
      if (score >= 0 && !seen.has(coin.base)) elsewhere.push({ exchange: name, coin, score, order });
    }
  });
  elsewhere.sort((a, b) => a.score - b.score || a.order - b.order || b.coin.quote_volume - a.coin.quote_volume);
  const picked = [];
  for (const hit of elsewhere) {
    if (picked.some((item) => item.coin.base === hit.coin.base)) continue;
    picked.push({ exchange: hit.exchange, coin: hit.coin });
  }
  return { here: current.slice(0, here), other: picked.slice(0, other) };
}

// 거래대금 순위에서 고른 코인 앞뒤(기본 5개) — 순위 칸의 코인 정보 아래에 둔다.
export function neighborsByTurnover(coins, base, size = 5) {
  const sorted = sortCoins(coins, "turnover");
  const index = sorted.findIndex((coin) => coin.base === base);
  if (index < 0) return [];
  const start = Math.max(0, Math.min(index - Math.floor(size / 2), sorted.length - size));
  return sorted.slice(start, start + size).map((coin, offset) => ({ coin, rank: start + offset + 1 }));
}

export function turnoverRank(coins, base) {
  const index = sortCoins(coins, "turnover").findIndex((coin) => coin.base === base);
  return index < 0 ? null : index + 1;
}

// 해외 대비(%) — 원화 가격 ÷ (바이낸스 USDT 가격 × 환율) − 1.
export function premiumPct(krwPrice, usdtPrice, usdkrw) {
  if (![krwPrice, usdtPrice, usdkrw].every((value) => number(value) > 0)) return null;
  return (krwPrice / (usdtPrice * usdkrw) - 1) * 100;
}

export function rangePosition(price, low, high) {
  if (![price, low, high].every((value) => number(value) != null) || high <= low) return null;
  return Math.max(0, Math.min(1, (price - low) / (high - low)));
}

const KST_MS = 9 * 60 * 60 * 1000;
const timestamp = (value) => (typeof value === "number" ? value : Date.parse(value || ""));

// '방금 · n분 전 · n시간 전', 하루가 넘거나 날짜만 있는 글은 'M월 D일'(임의 시각을 붙이지 않는다).
export function relativeTime(value, { now = Date.now(), precision } = {}) {
  const ms = timestamp(value);
  if (!Number.isFinite(ms)) return "";
  const kst = new Date(ms + KST_MS);
  const date = `${kst.getUTCMonth() + 1}월 ${kst.getUTCDate()}일`;
  if (precision === "day" || /^\d{4}-\d{2}-\d{2}$/.test(String(value))) return date;
  const diff = now - ms;
  if (diff < 60_000) return "방금";
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}시간 전`;
  return date;
}

export function kstClock(value) {
  const ms = timestamp(value);
  if (!Number.isFinite(ms)) return "";
  const kst = new Date(ms + KST_MS);
  return [kst.getUTCHours(), kst.getUTCMinutes(), kst.getUTCSeconds()].map((n) => String(n).padStart(2, "0")).join(":");
}

export function kstDate(value) {
  const ms = timestamp(value);
  if (!Number.isFinite(ms)) return "";
  return new Date(ms + KST_MS).toISOString().slice(0, 10);
}

// 첫 쪽을 다시 받았을 때 — 기존 목록과 겹치는 글이 있으면 그 앞의 새 글만 위에 붙인다.
// 하나도 안 겹치면(새 글이 한 쪽보다 많이 쌓임) 중간이 빈 채로 이어 붙이지 않고 첫 쪽으로 갈아 끼운다.
export function refreshFirstPage(current, page, key = (item) => item.id || item.url) {
  const items = page || [];
  if (!current?.length) return { items: [...items], added: items.length, replaced: true };
  const known = new Set(current.map(key));
  const overlap = items.findIndex((item) => known.has(key(item)));
  if (overlap < 0) return { items: [...items], added: items.length, replaced: true };
  const fresh = items.slice(0, overlap);
  return { items: [...fresh, ...current], added: fresh.length, replaced: false };
}

// 안쪽 스크롤 막대 — 칸 위아래 inset 만큼 띄운 길 안에서 막대 길이(최소 28px)와 위치.
export function thumbGeometry({ clientHeight, scrollHeight, scrollTop }, { inset = 8, min = 28 } = {}) {
  const track = Math.max(0, clientHeight - inset * 2);
  const scrollable = scrollHeight > clientHeight + 1;
  const size = scrollable ? Math.min(track, Math.max(min, (track * clientHeight) / scrollHeight)) : track;
  const range = Math.max(1, scrollHeight - clientHeight);
  const offset = scrollable ? Math.max(0, Math.min(track - size, (scrollTop / range) * (track - size))) : 0;
  return { track, size, offset, scrollable, atTop: scrollTop <= 1, atEnd: scrollTop + clientHeight >= scrollHeight - 1 };
}

// 서버가 준 글 목록을 같은 글 없이 이어 붙인다(새로고침으로 앞쪽이 밀려도 겹치지 않게).
export function mergeById(current, incoming, key = (item) => item.id || item.url) {
  const seen = new Set((current || []).map(key));
  return [...(current || []), ...(incoming || []).filter((item) => {
    const id = key(item);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  })];
}
