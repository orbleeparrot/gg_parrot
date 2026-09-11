// The renderer consumes the same full buffer as indicators.js. Never round an
// OHLC value or slice away indicator warm-up data when adapting it to the chart.
export function candleData(candles, colors) {
  return candles.map((bar) => ({
    time: bar.t / 1000,
    open: bar.o, high: bar.h, low: bar.l, close: bar.c,
    ...(bar.closed ? {} : {
      color: bar.c >= bar.o ? colors.upFaded : colors.downFaded,
      wickColor: bar.c >= bar.o ? colors.upFaded : colors.downFaded,
    }),
  }));
}

export const sameCandleData = (a, b) => a && b && a.time === b.time && a.open === b.open && a.high === b.high && a.low === b.low && a.close === b.close && a.color === b.color;

export function canUpdateCandleData(previous, next) {
  return previous.length > 0 && next.length >= previous.length
    && previous.every((bar, i) => bar.time === next[i].time && (i >= previous.length - 2 || sameCandleData(bar, next[i])));
}

export function constrainCandleRange(range, total) {
  const span = Math.max(Math.min(10, total), Math.min(total, range.to - range.from));
  const to = Math.max(span - 0.5, Math.min(total - 0.5, range.to));
  return { from: to - span, to };
}

// A rolling 300-bar buffer changes logical indices. Anchor history by candle
// timestamp so an incoming bar doesn't move the period the user is inspecting.
export function restoreCandleRange(range, previous, next, live) {
  if (!range || !previous.length) {
    return { from: next.length - Math.min(80, next.length) - 0.5, to: next.length - 0.5 };
  }
  if (live) return constrainCandleRange({ from: next.length - 0.5 - (range.to - range.from), to: next.length - 0.5 }, next.length);
  const oldOrigin = previous.findIndex((bar) => bar.t === next[0]?.t);
  const newOrigin = next.findIndex((bar) => bar.t === previous[0]?.t);
  const offset = oldOrigin >= 0 ? -oldOrigin : newOrigin >= 0 ? newOrigin : -previous.length;
  return constrainCandleRange({ from: range.from + offset, to: range.to + offset }, next.length);
}

export function overlayPriceRange(info, candles, overlay, range) {
  if (!info || !candles.length) return info;
  const start = Math.max(0, Math.ceil(range?.from ?? 0));
  const end = Math.min(candles.length, Math.floor(range?.to ?? candles.length - 1) + 1);
  const low = info.priceRange.minValue, high = info.priceRange.maxValue;
  const span = high - low || Math.abs(high) * 0.001 || 1;
  let min = low, max = high;
  const include = (v) => {
    if (v == null || !Number.isFinite(v)) return;
    min = Math.min(min, Math.max(v, low - span * 1.5));
    max = Math.max(max, Math.min(v, high + span * 1.5));
  };
  overlay?.priceLines?.forEach((line) => include(line.price));
  overlay?.series?.forEach((line) => line.values.slice(start, end).forEach(include));
  overlay?.bands?.forEach((band) => {
    band.upper.slice(start, end).forEach(include);
    band.lower.slice(start, end).forEach(include);
  });
  return { ...info, priceRange: { minValue: min, maxValue: max } };
}

export function priceMinMove(price) {
  const abs = Math.abs(price);
  return abs >= 1000 ? 0.01 : abs >= 1 ? 0.0001 : abs >= 0.01 ? 0.00001 : abs >= 0.0001 ? 0.000001 : 0.00000001;
}
