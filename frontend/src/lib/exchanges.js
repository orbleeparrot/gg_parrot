import { quoteOf } from "./format.js";

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

// A run session has no exchange column, only its symbol — and a domestic symbol is always `KRW-<COIN>`.
// The session's money is therefore in KRW when the symbol says so, and in the Binance quote otherwise.
// `quoteOf` (lib/format.js) already reads that prefix, so there is one rule for it; these helpers are the
// session-facing side: the amount with its unit, and the practice-mode name that exists on that exchange.
export const isDomesticSymbol = (symbol) => quoteOf(symbol) === "KRW";

// Won has no minor unit worth showing; a quote-asset amount keeps two decimals.
export const quoteDigits = (quote) => (quote === "KRW" ? 0 : 2);

// "100,000 KRW" · "12.5 USDT". `fixed` pads to the unit's digits (profit columns); without it trailing zeros drop.
export function formatQuoteAmount(value, symbol, { fixed = false } = {}) {
  const quote = quoteOf(symbol);
  const digits = quoteDigits(quote);
  const body = (Number(value) || 0).toLocaleString("en-US", { minimumFractionDigits: fixed ? digits : 0, maximumFractionDigits: digits });
  return `${body} ${quote}`;
}

// The runner reports `testnet: true` for every practice session. On Binance that is the testnet; Upbit and
// Bithumb have no testnet, so the same flag means the runner's mock mode (no orders are placed).
export function practiceModeLabel(symbol) {
  return isDomesticSymbol(symbol) ? "모의" : "테스트넷";
}

// This convenience normalization is for a typed base ticker, not for validating
// uploaded macro JSON. Actual market existence is always checked by the server.
export function normalizeSymbolForExchange(value, exchange = "binance") {
  const symbol = String(value || "").trim().toUpperCase();
  if (!symbol) return "";
  if (isDomestic(exchange)) return symbol.startsWith("KRW-") ? symbol : /^[A-Z0-9]+$/.test(symbol) ? `KRW-${symbol}` : symbol;
  return symbol.endsWith("USDT") ? symbol : /^[A-Z0-9]+$/.test(symbol) ? `${symbol}USDT` : symbol;
}
