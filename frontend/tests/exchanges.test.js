import test from "node:test";
import assert from "node:assert/strict";
import { EXCHANGES, normalizeExchange, quoteForExchange, isDomestic, marketKey, normalizeSymbolForExchange } from "../src/lib/exchanges.js";
import { baseOf, quoteOf, fmtMoney, fmtKrw } from "../src/lib/format.js";
import { defaultForm, buildMacro, macroToForm, validateDetailed, withExchangeDefaults, withTypeDefaults } from "../src/lib/macro.js";
import { resolveSymbol, searchSymbols } from "../src/lib/symbolSearch.js";

test("exchange identity is explicit and unknown providers are rejected", () => {
  assert.deepEqual(EXCHANGES.map((item) => item.value), ["binance", "upbit", "bithumb"]);
  assert.equal(normalizeExchange(), "binance");
  assert.equal(normalizeExchange(" UPBIT "), "upbit");
  assert.throws(() => normalizeExchange("other"));
  assert.equal(quoteForExchange("binance"), "USDT");
  assert.equal(quoteForExchange("bithumb"), "KRW");
  assert.equal(isDomestic("upbit"), true);
  assert.notEqual(marketKey("upbit", "KRW-BTC"), marketKey("bithumb", "KRW-BTC"));
});

test("native Korean markets have a KRW quote and the correct coin base", () => {
  assert.equal(baseOf("KRW-BTC"), "BTC");
  assert.equal(quoteOf("KRW-BTC"), "KRW");
  assert.equal(fmtMoney(1000000, "KRW-BTC"), "1,000,000 KRW");
  assert.equal(fmtKrw(1000000, 1400, "KRW-BTC"), "");
  assert.equal(fmtKrw(100, 1400, "BTCUSDT"), "≈ 14만원");
  assert.equal(baseOf("BTCUSDT"), "BTC");
  assert.equal(normalizeSymbolForExchange("btc", "upbit"), "KRW-BTC");
  assert.equal(normalizeSymbolForExchange("KRW-BTC", "bithumb"), "KRW-BTC");
  assert.equal(normalizeSymbolForExchange("btc", "binance"), "BTCUSDT");
});

test("domestic symbol search retains native identity and exclusions", () => {
  const items = [
    { symbol: "KRW-BTC", base: "BTC", quote: "KRW", spot: true, futures: false },
    { symbol: "KRW-ETH", base: "ETH", quote: "KRW", spot: true, futures: false },
  ];
  assert.equal(resolveSymbol(items, "KRW-BTC"), "KRW-BTC");
  assert.equal(resolveSymbol(items, "btc"), "KRW-BTC");
  assert.deepEqual(searchSymbols(items, "krw-btc").map((item) => item.symbol), ["KRW-BTC"]);
  assert.deepEqual(searchSymbols(items, "BTC", { exclude: ["KRW-BTC"] }), []);
});

test("legacy Binance macros retain their exchange and amounts on round trip", () => {
  const form = defaultForm();
  const macro = buildMacro(form);
  assert.equal(macro.exchange, "binance");
  assert.equal(macro.quote_currency, "USDT");
  const { exchange, quote_currency, ...legacy } = macro;
  const loaded = macroToForm(legacy);
  assert.equal(loaded.exchange, "binance");
  assert.equal(loaded.symbol, "BTCUSDT");
  assert.equal(loaded.initial_capital, form.initial_capital);
});

test("changing exchange requires re-entering prices and money, with no FX conversion", () => {
  const form = { ...defaultForm(), rule_type: "K", position_side: "short", leverage: 3, market: "futures", funding_pct: 1 };
  const switched = withExchangeDefaults(form, "upbit");
  assert.equal(switched.exchange, "upbit");
  assert.equal(switched.symbol, "");
  for (const field of ["initial_capital", "amount_per_buy", "buy_price", "sell_price", "lower_price", "upper_price", "base_order_size", "safety_order_size"]) assert.equal(switched[field], "");
  assert.equal(switched.position_side, "long");
  assert.equal(switched.leverage, 1);
  assert.equal(switched.market, "spot");
  assert.equal(switched.funding_pct, 0);
  assert.equal(switched.flip_to_short, false);
  assert.equal(switched.rule_type, "A");
  assert.equal(validateDetailed(switched)?.field, "symbol");
  assert.equal(withExchangeDefaults(form, "binance"), form);
});

test("domestic macros round-trip and reject short, leverage, futures, funding and SAR bypasses", () => {
  const valid = { ...defaultForm(), exchange: "upbit", symbol: "KRW-BTC", market: "spot", flip_to_short: false };
  assert.equal(validateDetailed(valid), null);
  const macro = buildMacro(valid);
  assert.equal(macro.exchange, "upbit");
  assert.equal(macro.quote_currency, "KRW");
  assert.equal(macroToForm(macro).exchange, "upbit");
  for (const bad of [{ position_side: "short" }, { leverage: 2 }, { market: "futures" }, { funding_pct: 1 }, { rule_type: "K" }]) assert.ok(validateDetailed({ ...valid, ...bad }));
  assert.equal(validateDetailed({ ...valid, exchange: "other" })?.field, "exchange");
  assert.equal(validateDetailed({ ...valid, symbol: "BTCUSDT" })?.field, "symbol");
  assert.equal(validateDetailed({ ...valid, initial_capital: "" })?.field, "initial_capital");
  assert.equal(withTypeDefaults(valid, "F").position_side, "long");
});

test("DCA retains the entered account budget and domestic DCA uses daily candles", () => {
  const form = { ...defaultForm(), exchange: "bithumb", symbol: "KRW-BTC", initial_capital: 1234567, amount_per_buy: 10000, market: "spot", flip_to_short: false };
  const dca = withTypeDefaults(form, "C");
  assert.equal(dca.candle_interval, "1d");
  assert.equal(dca.initial_capital, 1234567);
  const macro = buildMacro(dca);
  assert.equal(macro.params.initial_capital, 1234567);
  assert.equal(macroToForm(macro).initial_capital, 1234567);
  assert.equal(validateDetailed({ ...dca, candle_interval: "1h" })?.field, "candle_interval");
});
