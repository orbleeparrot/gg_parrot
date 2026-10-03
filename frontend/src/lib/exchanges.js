// Exchange identity is separate from spot/futures. Symbols retain their native
// exchange format; equal KRW pairs on two exchanges are not the same market.
export const EXCHANGES = Object.freeze([
  { value: "binance", label: "바이낸스", quote: "USDT" },
  { value: "upbit", label: "업비트", quote: "KRW" },
  { value: "bithumb", label: "빗썸", quote: "KRW" },
]);

export function normalizeExchange(value) {
  const exchange = value == null || value === "" ? "binance" : String(value).trim().toLowerCase();
  if (!EXCHANGES.some((item) => item.value === exchange)) throw new Error("지원하지 않는 거래소예요.");
  return exchange;
}

export const exchangeLabel = (value) => EXCHANGES.find((item) => item.value === normalizeExchange(value)).label;
export const quoteForExchange = (value) => EXCHANGES.find((item) => item.value === normalizeExchange(value)).quote;
export const exchangeLogo = (value) => { const exchange = normalizeExchange(value); return `/exchanges/${exchange}.${exchange === "binance" ? "svg" : "png"}`; };
export const isDomestic = (value) => normalizeExchange(value) !== "binance";
export const marketKey = (exchange, symbol) => `${normalizeExchange(exchange)}:${String(symbol || "").trim().toUpperCase()}`;

// This convenience normalization is for a typed base ticker, not for validating
// uploaded macro JSON. Actual market existence is always checked by the server.
export function normalizeSymbolForExchange(value, exchange = "binance") {
  const symbol = String(value || "").trim().toUpperCase();
  if (!symbol) return "";
  if (isDomestic(exchange)) return symbol.startsWith("KRW-") ? symbol : /^[A-Z0-9]+$/.test(symbol) ? `KRW-${symbol}` : symbol;
  return symbol.endsWith("USDT") ? symbol : /^[A-Z0-9]+$/.test(symbol) ? `${symbol}USDT` : symbol;
}
