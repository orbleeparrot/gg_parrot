// Money/price/quantity formatting.
//
// Unit basis: prices use the exchange's native quote asset. qty = capital / price, so all
// money amounts (initial_capital, equity, amount_per_buy) are denominated in
// that quote currency — USDT on Binance or KRW on domestic KRW markets. `qty` is a COIN COUNT.

const QUOTES = ["USDT", "BUSD", "USDC", "FDUSD", "TUSD", "USD", "KRW"];

export function quoteOf(symbol) {
  const s = (symbol || "").toUpperCase();
  if (s.startsWith("KRW-")) return "KRW";
  for (const q of QUOTES) if (s.endsWith(q)) return q;
  return "USDT";
}

export function baseOf(symbol) {
  const s = (symbol || "").toUpperCase();
  if (s.startsWith("KRW-")) return s.slice(4);
  const q = quoteOf(s);
  return s.endsWith(q) ? s.slice(0, -q.length) : s;
}

// Price: keep enough significant decimals for sub-cent coins (VANRY ~0.0077).
export function fmtPrice(p) {
  const n = Number(p);
  if (!isFinite(n)) return String(p);
  const abs = Math.abs(n);
  let dp;
  if (abs >= 1000) dp = 2;
  else if (abs >= 1) dp = 4;
  else if (abs >= 0.01) dp = 5;
  else if (abs >= 0.0001) dp = 6;
  else dp = 8;
  return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: dp });
}

// Money amount + quote unit, e.g. "1,000,000 USDT".
export function fmtMoney(value, symbol) {
  const n = Number(value);
  const q = quoteOf(symbol);
  return `${n.toLocaleString("en-US", { maximumFractionDigits: 2 })} ${q}`;
}

// Compact money for tight stat boxes: large amounts abbreviate to K/M/B so the
// value never overflows its card. Pair with a `title` of the exact fmtMoney
// value so the precise number stays available on hover.
export function fmtMoneyCompact(value, symbol) {
  const n = Number(value);
  const q = quoteOf(symbol);
  if (!isFinite(n)) return `${value} ${q}`;
  const abs = Math.abs(n);
  let s;
  if (abs >= 1e9) s = `${(n / 1e9).toFixed(2)}B`;
  else if (abs >= 1e6) s = `${(n / 1e6).toFixed(2)}M`;
  else s = n.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return `${s} ${q}`;
}

// Approximate KRW for a USDT amount, e.g. "≈ 138만원". REFERENCE ONLY — the app
// is denominated in USDT; this is a rough convenience conversion at `rate`
// (USD→KRW). Returns "" when the rate is missing so callers can skip rendering.
export function fmtKrw(usdtValue, rate, symbol = "") {
  if (quoteOf(symbol) === "KRW") return ""; // Never convert native won amounts again.
  const won = Number(usdtValue) * Number(rate);
  if (!isFinite(won) || !isFinite(Number(rate)) || Number(rate) <= 0) return "";
  return `≈ ${krwShort(won)}`;
}

// Compact Korean money with 만/억 units so big amounts stay readable.
function krwShort(won) {
  const sign = won < 0 ? "-" : "";
  const abs = Math.abs(won);
  if (abs >= 1e8) {
    return `${sign}${(abs / 1e8).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}억원`;
  }
  if (abs >= 1e4) {
    return `${sign}${(abs / 1e4).toLocaleString("ko-KR", { maximumFractionDigits: 1 })}만원`;
  }
  return `${sign}${Math.round(abs).toLocaleString("ko-KR")}원`;
}

// Coin quantity (with the coin ticker), e.g. "130,396,720.22 VANRY".
export function fmtQty(qty, symbol) {
  const n = Number(qty);
  const abs = Math.abs(n);
  const dp = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  const num = n.toLocaleString("en-US", { maximumFractionDigits: dp });
  return symbol ? `${num} ${baseOf(symbol)}` : num;
}
