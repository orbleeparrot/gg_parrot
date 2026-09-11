// The strategy calculation remains in indicators.js. This adapter only maps its
// values to chart coordinates, retaining the original line styles and signals.
const DEFAULT_COLORS = {
  up: "rgb(0 119 56)",
  down: "rgb(200 30 51)",
  text: "rgb(148 163 184)",
  muted: "rgb(148 163 184)",
  surface: "rgb(11 14 17)",
  rsi: "rgb(99 102 241)",
  fontFamily: "sans-serif",
};

const finite = (value) => value != null && Number.isFinite(value);
const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
export const RSI_SCALE_MARGINS = Object.freeze({ top: .08, bottom: .08 });

// Call while updating a theme, never in a renderer. Substitution keeps alpha
// and space-separated rgb() notation intact without creating probe elements.
export function resolveChartColor(color, element) {
  if (!color) return DEFAULT_COLORS.muted;
  if (!color.includes("var(") && color !== "currentColor") return color;
  const view = element?.ownerDocument?.defaultView;
  if (!view) return DEFAULT_COLORS.muted;
  const style = view.getComputedStyle(element);
  if (color === "currentColor") return style.color;
  const resolved = color.replace(/var\(\s*(--[\w-]+)\s*(?:,\s*([^()]+))?\)/g, (_, name, fallback) => (
    style.getPropertyValue(name).trim() || fallback?.trim() || ""
  ));
  return resolved.includes("var(") || /\(\s*\)/.test(resolved) ? DEFAULT_COLORS.muted : resolved;
}

function dashPattern(value) {
  if (!value) return [];
  const numbers = Array.isArray(value) ? value : String(value).trim().split(/[\s,]+/).map(Number);
  return numbers.every((number) => finite(number) && number >= 0) && numbers.some((number) => number > 0)
    ? numbers : [];
}

function polygon(context, points, color) {
  if (points.length < 3) return;
  context.fillStyle = color;
  context.beginPath();
  points.forEach(([x, y], index) => index ? context.lineTo(x, y) : context.moveTo(x, y));
  context.closePath();
  context.fill();
}

function horizontal(context, y, width, color, dash, lineWidth = 1.25, opacity = .9) {
  context.strokeStyle = color;
  context.lineWidth = lineWidth;
  context.globalAlpha = opacity;
  context.setLineDash(dash);
  context.beginPath();
  context.moveTo(0, y);
  context.lineTo(width, y);
  context.stroke();
  context.globalAlpha = 1;
}

/**
 * A public Lightweight Charts series primitive. Attach kind="price" to the
 * candle series and kind="rsi" to a separate series with a fixed 0–100 scale.
 * `candles` must be the complete history used to calculate `overlay`, so marker
 * indices and indicator warm-up gaps survive panning, zooming, and live ticks.
 *
 * colors.resolve, when supplied, receives each distinct source color once per
 * setData call. Draws never read CSS or modify the original overlay arrays.
 */
export class StrategyPrimitive {
  constructor({ candles = [], overlay = null, colors = {}, kind = "price" } = {}) {
    this.kind = kind;
    this._views = ["bottom", "normal"].map((layer) => ({
      zOrder: () => layer,
      renderer: () => ({
        draw: (target) => target.useMediaCoordinateSpace((scope) => this._draw(scope, layer)),
      }),
    }));
    this.setData({ candles, overlay, colors });
  }

  attached({ chart, series, requestUpdate }) {
    this._chart = chart;
    this._series = series;
    this._requestUpdate = requestUpdate;
    requestUpdate();
  }

  detached() {
    this._chart = null;
    this._series = null;
    this._requestUpdate = null;
  }

  paneViews() {
    return this._views;
  }

  setData({ candles = this.candles, overlay = this.overlay, colors = this.colors } = {}) {
    this.candles = candles || [];
    this.overlay = overlay;
    this.colors = { ...DEFAULT_COLORS, ...colors };
    const cache = new Map([
      ["rgb(var(--chart-up))", this.colors.up],
      ["rgb(var(--chart-down))", this.colors.down],
      ["rgb(var(--c-surface))", this.colors.surface],
    ]);
    const color = (source) => {
      if (!source) return this.colors.muted;
      if (!cache.has(source)) {
        const resolved = this.colors.resolve ? this.colors.resolve(source) : source;
        cache.set(source, resolved?.includes("var(") ? this.colors.muted : resolved || this.colors.muted);
      }
      return cache.get(source);
    };
    this._bands = (overlay?.bands || []).map((band) => ({ ...band, fill: color(band.fill) }));
    this._lines = (overlay?.series || []).map((line) => ({
      ...line, color: color(line.color), dash: dashPattern(line.dash),
    }));
    this._priceLines = (overlay?.priceLines || []).map((line) => ({
      ...line, color: color(line.color), dash: dashPattern(line.dash),
    }));
    this._rsi = overlay?.rsi || (this.kind === "rsi" ? overlay : null);
    this._requestUpdate?.();
  }

  // Price scaling belongs to the candle series. In particular, remote limit
  // prices must not squash the candles by extending the primitive's autoscale.
  autoscaleInfo() {
    return null;
  }

  _coordinates(height) {
    if (!this._chart || !this._series || !this.candles.length) return null;
    const timeScale = this._chart.timeScale();
    const visible = timeScale.getVisibleLogicalRange();
    if (!visible) return null;
    // One neighbour per side preserves a line/band crossing the viewport edge.
    const start = Math.max(0, Math.floor(visible.from) - 1);
    const end = Math.min(this.candles.length - 1, Math.ceil(visible.to) + 1);
    const xs = new Map();
    const x = (index) => {
      if (!xs.has(index)) {
        const candle = this.candles[index];
        xs.set(index, candle ? timeScale.timeToCoordinate(candle.t / 1000) : null);
      }
      return xs.get(index);
    };
    const y = (value) => {
      if (!finite(value)) return null;
      const coordinate = this._series.priceToCoordinate(value);
      if (finite(coordinate) || this.kind !== "rsi") return coordinate;
      // An all-whitespace RSI has no native firstValue/price coordinate yet.
      // Keep its fixed reference zones visible without manufacturing a numeric
      // RSI point that the crosshair could mistake for a calculated value.
      // Match Lightweight Charts' linear 0–100 projection, including margins.
      const innerHeight = height * (1 - RSI_SCALE_MARGINS.top - RSI_SCALE_MARGINS.bottom);
      return height - 1 - height * RSI_SCALE_MARGINS.bottom - (innerHeight - 1) * value / 100;
    };
    return { start, end, x, y };
  }

  _draw({ context, mediaSize }, layer) {
    const coords = this._coordinates(mediaSize.height);
    if (!coords || !mediaSize.width || !mediaSize.height) return;
    context.save();
    context.beginPath();
    context.rect(0, 0, mediaSize.width, mediaSize.height);
    context.clip();
    context.lineJoin = "round";
    if (this.kind === "rsi") this._drawRsi(context, mediaSize, coords, layer);
    else if (layer === "bottom") this._drawBands(context, coords);
    else this._drawPrice(context, mediaSize, coords);
    context.restore();
  }

  _drawBands(context, { start, end, x, y }) {
    for (const band of this._bands) {
      let upper = [];
      let lower = [];
      const flush = () => {
        polygon(context, [...upper, ...lower.reverse()], band.fill);
        upper = [];
        lower = [];
      };
      for (let index = start; index <= end; index++) {
        const xx = x(index);
        const top = y(band.upper[index]);
        const bottom = y(band.lower[index]);
        if (!finite(xx) || !finite(top) || !finite(bottom)) {
          flush();
          continue;
        }
        upper.push([xx, top]);
        lower.push([xx, bottom]);
      }
      flush();
    }
  }

  _drawLine(context, { values, color, width = 1.25, dash = [] }, { start, end, x, y }, opacity = .95) {
    context.strokeStyle = color;
    context.lineWidth = width;
    context.globalAlpha = opacity;
    context.setLineDash(dash);
    context.beginPath();
    let connected = false;
    for (let index = start; index <= end; index++) {
      const xx = x(index);
      const yy = y(values[index]);
      if (!finite(xx) || !finite(yy)) {
        connected = false;
        continue;
      }
      if (connected) context.lineTo(xx, yy);
      else context.moveTo(xx, yy);
      connected = true;
    }
    context.stroke();
    context.globalAlpha = 1;
  }

  _drawPrice(context, { width, height }, coords) {
    const { start, end, x, y } = coords;
    this._lines.forEach((line) => this._drawLine(context, line, coords));
    const tags = [];
    for (const line of this._priceLines) {
      const yy = y(line.price);
      if (!finite(yy)) continue;
      if (yy >= -2 && yy <= height + 2) horizontal(context, yy, width, line.color, line.dash);
      // The original chart omits out-of-scale labels instead of pinning a remote
      // order price onto the edge and implying that it is inside the view.
      if (line.label && yy >= 12 && yy <= height - 4) tags.push({ y: yy - 2, label: line.label, color: line.color });
    }
    for (const marker of this.overlay?.markers || []) {
      if (marker.index < start || marker.index > end) continue;
      const bar = this.candles[marker.index];
      if (!bar) continue;
      const xx = x(marker.index);
      const buy = marker.side === "buy";
      const edge = y(buy ? bar.l : bar.h);
      if (!finite(xx) || !finite(edge)) continue;
      const yy = edge + (buy ? 9 : -9);
      polygon(context, [[xx, yy + (buy ? -7 : 7)], [xx + 4, yy], [xx - 4, yy]], buy ? this.colors.up : this.colors.down);
    }
    context.setLineDash([]);
    context.font = `700 11px ${this.colors.fontFamily}`;
    context.textAlign = "right";
    context.textBaseline = "alphabetic";
    context.lineWidth = 3;
    context.strokeStyle = this.colors.surface;
    // Sort visually, then move colliding labels as a group into the pane. Labels
    // retain their order and full text rather than overlapping one another.
    tags.sort((a, b) => a.y - b.y);
    tags.forEach((tag, index) => { tag.y = Math.max(tag.y, index ? tags[index - 1].y + 13 : 12); });
    if (tags.length) {
      const overflow = Math.max(0, tags[tags.length - 1].y - (height - 4));
      tags.forEach((tag) => { tag.y -= overflow; });
    }
    for (const tag of tags) {
      context.fillStyle = tag.color;
      context.strokeText(tag.label, width - 4, tag.y);
      context.fillText(tag.label, width - 4, tag.y);
    }
  }

  _drawRsi(context, { width, height }, coords, layer) {
    if (!this._rsi) return;
    const { entry, exit, lowLabel, highLabel, values = [] } = this._rsi;
    const upper = coords.y(exit);
    const lower = coords.y(entry);
    if (layer === "bottom") {
      if (finite(upper)) {
        context.fillStyle = "rgba(200,30,51,0.06)";
        context.fillRect(0, 0, width, clamp(upper, 0, height));
      }
      if (finite(lower)) {
        context.fillStyle = "rgba(0,119,56,0.06)";
        const top = clamp(lower, 0, height);
        context.fillRect(0, top, width, height - top);
      }
      return;
    }
    if (finite(upper)) horizontal(context, upper, width, this.colors.down, [4, 3], 1, .8);
    if (finite(lower)) horizontal(context, lower, width, this.colors.up, [4, 3], 1, .8);
    this._drawLine(context, { values, color: this.colors.rsi, width: 1.5 }, coords, 1);
    context.setLineDash([]);
    context.font = `500 11px ${this.colors.fontFamily}`;
    // Attribution sits at the lower left of the last pane. Keep both threshold
    // labels on the right so the oversold/action text remains unobstructed.
    context.textAlign = "right";
    context.textBaseline = "alphabetic";
    context.strokeStyle = this.colors.surface;
    context.lineWidth = 3;
    for (const [yy, label, color] of [
      [finite(upper) ? upper - 4 : null, `과매수 ${exit}${highLabel ? ` · ${highLabel}` : ""}`, this.colors.down],
      [finite(lower) ? lower + 12 : null, `과매도 ${entry}${lowLabel ? ` · ${lowLabel}` : ""}`, this.colors.up],
    ]) {
      if (!finite(yy)) continue;
      const position = clamp(yy, 11, height - 3);
      context.fillStyle = color;
      context.strokeText(label, width - 4, position);
      context.fillText(label, width - 4, position);
    }
  }
}
