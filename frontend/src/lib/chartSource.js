import { isDomestic, normalizeExchange } from "./exchanges.js";

export const CHART_FRESH_MS = 15_000;
const BUFFER = 300;
const kst = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Seoul", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const kstParts = (ms) => Object.fromEntries(kst.formatToParts(new Date(ms)).map(({ type, value }) => [type, value]));
export const chartTimeKst = (ms) => { const p = kstParts(ms); return `${Number(p.month)}/${Number(p.day)} ${p.hour}:${p.minute}`; };
export const chartDateKst = (ms) => { const p = kstParts(ms); return `${Number(p.month)}/${Number(p.day)}`; };
export const chartClockKst = (ms) => { const p = kstParts(ms); return `${p.hour}:${p.minute}`; };

export function resolveChartMarket(macro = {}) {
  if (isDomestic(macro.exchange)) return "spot";
  if (macro.market === "spot" || macro.market === "futures") return macro.market;
  return macro.rule_type === "K" || macro.position_side === "short" || Number(macro.leverage || 1) > 1 ? "futures" : "spot";
}

const sourceKey = (source) => `${source.exchange}:${source.symbol}:${source.interval}:${source.market}`;
const positive = (value) => Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 0;

// Response identity, not the requested market, decides which prices we display.
// Legacy responses without provenance may render, but never claim freshness
// unless they include a usable origin fetch timestamp.
export function chartResponseMeta(payload, request, receivedAt = Date.now()) {
  const exchange = normalizeExchange(payload.exchange ?? request.exchange);
  const symbol = String(payload.symbol ?? request.symbol).toUpperCase();
  const interval = payload.interval ?? request.interval;
  const market = payload.market ?? request.market;
  if (exchange !== request.exchange || symbol !== request.symbol || interval !== request.interval || !["spot", "futures"].includes(market) || (isDomestic(exchange) && market !== "spot")) {
    throw new Error("응답 시세의 거래소·종목·봉 간격·시장이 요청과 달라 적용하지 않았어요.");
  }
  const fetchedAt = positive(payload.fetched_at_ms);
  const legacy = !Object.hasOwn(payload, "fetched_at_ms");
  const legacyServerTime = positive(payload.server_time);
  const legacyAge = legacyServerTime ? receivedAt - legacyServerTime : Infinity;
  const explicitIdentity = ["exchange", "symbol", "interval", "market"].every((key) => payload[key] != null);
  // The old backend cached its prepare-time server_time. It may order price
  // updates during a staggered deploy, but is not a claimed source fetch time.
  // Do not substitute the local receive clock for missing/invalid timestamps.
  const priceOrigin = fetchedAt || (legacy && explicitIdentity && legacyAge >= 0 && legacyAge <= CHART_FRESH_MS ? legacyServerTime : 0);
  const reportedAge = Number(payload.cache_age_ms);
  const cacheAge = payload.cache_age_ms != null && Number.isFinite(reportedAge) && reportedAge >= 0
    ? reportedAge : fetchedAt ? Math.max(0, receivedAt - fetchedAt) : Infinity;
  return { exchange, symbol, interval, market, requestedMarket: request.market,
    fallback: market !== request.market, fetchedAt, priceOrigin, legacy, legacyAge, receivedAt, cacheAge, serverTime: positive(payload.server_time) || receivedAt,
    stale: !!payload.stale, awaiting: !!payload.awaiting_candle_refresh, cached: !!payload.cached };
}

export function isChartFresh(meta, now = Date.now()) {
  return !!meta && meta.fetchedAt > 0 && !meta.stale && !meta.awaiting &&
    meta.cacheAge + Math.max(0, now - meta.receivedAt) <= CHART_FRESH_MS;
}

function canUseLivePrices(meta, now) {
  return isChartFresh(meta, now) || (meta.legacy && meta.priceOrigin > 0 && !meta.stale && !meta.awaiting &&
    meta.legacyAge + Math.max(0, now - meta.receivedAt) <= CHART_FRESH_MS);
}

export function isChartLive(meta, bar, now = Date.now()) {
  const steps = { "1m": 60000, "5m": 300000, "15m": 900000, "1h": 3600000, "4h": 14400000, "1d": 86400000 };
  const serverNow = (meta?.serverTime || now) + Math.max(0, now - (meta?.receivedAt || now));
  return isChartFresh(meta, now) && bar?.closed === false && Number.isFinite(bar.t) &&
    bar.t <= serverNow && bar.t + steps[meta.interval] > serverNow;
}

function merge(history, latest) {
  const rows = new Map(history.map((bar) => [bar.t, bar]));
  latest.forEach((bar) => rows.set(bar.t, bar));
  return Array.from(rows.values()).sort((a, b) => a.t - b.t).slice(-BUFFER);
}

export function createChartStream(request) {
  return { request: { ...request, exchange: normalizeExchange(request.exchange), symbol: String(request.symbol).trim().toUpperCase() }, source: null, revision: 0, candles: [], history: null, live: null, liveData: null };
}

export function applyChartHistory(stream, payload, receivedAt = Date.now()) {
  const meta = chartResponseMeta(payload, stream.request, receivedAt);
  const sameSource = stream.source && sourceKey(stream.source) === sourceKey(meta);
  const newerLive = sameSource && stream.liveData && stream.liveData.priceOrigin > meta.priceOrigin;
  const candles = Array.isArray(payload.candles) ? payload.candles : [];
  // Only a source switch retires requests. Full/live polls may start together
  // every three seconds; retiring every history refresh would discard the
  // fresher live response forever when history consistently finishes first.
  return { ...stream, source: meta, revision: stream.revision + (sameSource ? 0 : 1),
    candles: newerLive ? merge(candles, stream.candles.slice(-2)) : candles,
    history: meta, live: newerLive ? stream.live : null, liveData: newerLive ? stream.liveData : null };
}

export function applyChartLive(stream, payload, capturedRevision, receivedAt = Date.now()) {
  // Even A -> B -> A source switches retire earlier in-flight requests.
  if (!stream.source || capturedRevision !== stream.revision) return { stream, ignored: true, reload: false };
  const meta = chartResponseMeta(payload, stream.request, receivedAt);
  if (sourceKey(meta) !== sourceKey(stream.source)) return { stream, ignored: true, reload: true };
  const latestOrigin = Math.max(stream.history?.priceOrigin || 0, stream.liveData?.priceOrigin || 0);
  const old = !canUseLivePrices(meta, receivedAt) || meta.priceOrigin < latestOrigin;
  return { stream: { ...stream,
    candles: old ? stream.candles : merge(stream.candles, Array.isArray(payload.candles) ? payload.candles : []),
    // Channel status can describe a failed/stale attempt without erasing the
    // origin timestamp of the last accepted live prices.
    live: meta, liveData: old ? stream.liveData : meta }, ignored: old,
    reload: !!(stream.history?.stale || stream.history?.awaiting) && !old };
}
