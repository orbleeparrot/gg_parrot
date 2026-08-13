import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtPrice, quoteOf } from "../lib/format.js";

// Dependency-free SVG candlestick chart (same approach as EquityChart).
//
// Full history and the moving edge use separate polling loops. Long intervals
// can keep their 300-bar history on a slower cadence, while the latest two bars
// are merged every few seconds so even a 1d chart still behaves live.
//
// Zoom/pan model: we always hold up to BUFFER bars and render a window of
// `zoom` bars ending at `anchor`. `anchor === null` means "pinned to the live
// edge" — new bars keep scrolling in. Panning back sets an explicit anchor so
// incoming data can't yank the view away while the user is inspecting.
//
// Overlays: an optional `overlay` prop (a function `(candles) => spec`, see
// lib/indicators.js) draws strategy helpers — bands, moving averages, limit
// lines, signal markers, an RSI subpane — on top of the candles so beginners
// can see where a macro would buy and sell.
const BUFFER = 300; // bars fetched (server clamps at CHART_MAX_LIMIT)
const MIN_ZOOM = 10; // fewest bars on screen (max detail)
const DEFAULT_ZOOM = 80;

function mergeLiveCandles(history, latest) {
  if (!Array.isArray(history) || !history.length || !Array.isArray(latest) || !latest.length) return history;
  const merged = new Map(history.map((bar) => [bar.t, bar]));
  latest.forEach((bar) => merged.set(bar.t, bar));
  return Array.from(merged.values()).sort((a, b) => a.t - b.t).slice(-BUFFER);
}

const INTERVALS = [
  { value: "1m", label: "1분" },
  { value: "5m", label: "5분" },
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
  { value: "1d", label: "1일" },
];

const UP = "rgb(var(--chart-up))";
const DOWN = "rgb(var(--chart-down))";

const pad2 = (n) => String(n).padStart(2, "0");

function fullTime(ms) {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// --- inspector panel: OHLC of the hovered (or latest) bar ---------------
function BarReadout({ bar }) {
  if (!bar) return null;
  const rise = bar.c >= bar.o;
  const pct = bar.o ? ((bar.c - bar.o) / bar.o) * 100 : 0;
  const cell = (label, v) => (
    <span className="candle-ohlc-cell whitespace-nowrap">
      <span className="text-slate-500">{label}</span>{" "}
      <span className="num font-semibold text-slate-900">{fmtPrice(v)}</span>
    </span>
  );
  return (
    <div className="candle-ohlc flex flex-wrap items-center gap-x-3 gap-y-1 t-caption">
      <span className="candle-ohlc-time text-slate-700 num">{fullTime(bar.t)}</span>
      {cell("시", bar.o)}
      {cell("고", bar.h)}
      {cell("저", bar.l)}
      {cell("종", bar.c)}
      <span className={"candle-ohlc-change font-bold num " + (rise ? "text-green-600" : "text-red-600")}>
        {rise ? "+" : ""}
        {pct.toFixed(2)}%
      </span>
      {!bar.closed && <span className="badge badge-flat">진행 중</span>}
    </div>
  );
}

// A colored swatch + label. Marker legends draw a small triangle instead of a bar.
function LegendItem({ item }) {
  return (
    <span className="inline-flex items-center gap-1.5 t-caption text-slate-600">
      {item.kind === "buy" || item.kind === "sell" ? (
        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
          {item.kind === "buy" ? (
            <path d="M5 1 L9 9 L1 9 Z" fill={item.color} />
          ) : (
            <path d="M5 9 L1 1 L9 1 Z" fill={item.color} />
          )}
        </svg>
      ) : (
        <span
          className="inline-block w-3.5 h-0"
          style={{ borderTop: `2px ${item.dash ? "dashed" : "solid"} ${item.color}` }}
        />
      )}
      {item.label}
    </span>
  );
}

// Build polyline segments from a null-gapped values array (breaks at nulls).
function segments(values, cx, y) {
  const out = [];
  let cur = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v == null || !Number.isFinite(v)) {
      if (cur.length > 1) out.push(cur);
      cur = [];
    } else {
      cur.push(`${cx(i)},${y(v)}`);
    }
  }
  if (cur.length > 1) out.push(cur);
  return out;
}

// Filled path between two null-gapped series (the contiguous defined tail).
function bandPath(upper, lower, cx, y) {
  const n = upper.length;
  let i0 = -1;
  for (let i = 0; i < n; i++) {
    if (upper[i] != null && lower[i] != null) {
      i0 = i;
      break;
    }
  }
  if (i0 < 0) return null;
  let d = `M ${cx(i0)} ${y(upper[i0])}`;
  for (let i = i0 + 1; i < n; i++) if (upper[i] != null) d += ` L ${cx(i)} ${y(upper[i])}`;
  for (let i = n - 1; i >= i0; i--) if (lower[i] != null) d += ` L ${cx(i)} ${y(lower[i])}`;
  return d + " Z";
}

function Chart({ candles, symbol, hover, setHover, onPan, overlay, expanded = false }) {
  const W = 720;
  const H = expanded ? 350 : 260;
  const pad = { l: 6, r: 6, t: 10, b: 10 };
  const n = candles.length;
  const svgRef = useRef(null);
  const drag = useRef(null);

  // Overlay values widen the price scale so bands/lines never clip.
  const extra = [];
  if (overlay) {
    overlay.series?.forEach((s) => s.values.forEach((v) => v != null && Number.isFinite(v) && extra.push(v)));
    overlay.bands?.forEach((b) => {
      b.upper.forEach((v) => v != null && Number.isFinite(v) && extra.push(v));
      b.lower.forEach((v) => v != null && Number.isFinite(v) && extra.push(v));
    });
    overlay.priceLines?.forEach((p) => Number.isFinite(p.price) && extra.push(p.price));
  }
  const hi = Math.max(...candles.map((k) => k.h), ...extra);
  const lo = Math.min(...candles.map((k) => k.l), ...extra);
  const span = hi - lo || hi * 0.001 || 1;
  const top = hi + span * 0.08; // breathing room so wicks never touch the frame
  const bot = lo - span * 0.08;
  const range = top - bot || 1;

  const plotW = W - pad.l - pad.r;
  const slot = plotW / n;
  const bw = Math.max(1, Math.min(slot * 0.68, 22));
  const cx = (i) => pad.l + slot * (i + 0.5);
  const y = (v) => pad.t + (1 - (v - bot) / range) * (H - pad.t - pad.b);

  const last = candles[n - 1];
  const first = candles[0];
  const up = last.c >= first.o;
  const guides = [0, 1, 2, 3].map((i) => bot + (range * (i + 0.5)) / 4);

  // Map a pointer event to a bar index (viewBox coords, so scale by rect width).
  const indexAt = useCallback(
    (evt) => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const vx = ((evt.clientX - rect.left) / rect.width) * W;
      const i = Math.floor((vx - pad.l) / slot);
      return i >= 0 && i < n ? i : null;
    },
    [n, slot]
  );

  // Pointer (not mouse) events so a finger can pan too. `touch-pan-y` hands us
  // horizontal drags while vertical swipes still scroll the page — with
  // `touch-none` the chart was a dead zone the page couldn't be scrolled past.
  function handleDown(e) {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    drag.current = { x: e.clientX, panned: false };
    if (e.pointerType === "mouse") setHover(null);
  }

  function handleMove(e) {
    if (drag.current !== null) {
      const rect = svgRef.current?.getBoundingClientRect();
      if (rect) {
        const perBar = rect.width / n;
        const moved = Math.round((drag.current.x - e.clientX) / perBar);
        if (moved !== 0) {
          onPan(moved);
          drag.current = { x: e.clientX, panned: true };
        }
      }
      return;
    }
    if (e.pointerType === "mouse") setHover(indexAt(e));
  }

  function handleUp(e) {
    const d = drag.current;
    drag.current = null;
    // A tap that didn't pan inspects the bar under the finger.
    if (d && !d.panned && e.pointerType !== "mouse") setHover(indexAt(e));
  }

  // Right-edge tags for price lines. Stagger vertically when two labels collide.
  const priceTags = [];
  if (overlay?.priceLines) {
    const placed = [];
    for (const line of overlay.priceLines) {
      if (!line.label || !Number.isFinite(line.price)) continue;
      let ty = y(line.price);
      if (ty < pad.t + 6 || ty > H - pad.b - 2) continue; // off-scale: skip tag (line still draws)
      while (placed.some((p) => Math.abs(p - ty) < 11)) ty -= 11;
      placed.push(ty);
      priceTags.push({ ty, label: line.label, color: line.color });
    }
  }

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-auto touch-pan-y select-none cursor-crosshair"
      role="img"
      aria-label={`${symbol} 봉차트. 좌우 화살표로 봉별 값을 확인할 수 있어요.`}
      tabIndex={0}
      onPointerMove={handleMove}
      onPointerLeave={(e) => {
        if (e.pointerType === "mouse") setHover(null);
        drag.current = null;
      }}
      onPointerDown={handleDown}
      onPointerUp={handleUp}
      onPointerCancel={() => (drag.current = null)}
      onKeyDown={(event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) return;
        event.preventDefault();
        if (event.key === "Escape") return setHover(null);
        if (event.key === "Home") return setHover(0);
        if (event.key === "End") return setHover(n - 1);
        setHover((current) => {
          const from = current == null ? n - 1 : current;
          return Math.max(0, Math.min(n - 1, from + (event.key === "ArrowRight" ? 1 : -1)));
        });
      }}
    >
      {guides.map((v, i) => (
        <line key={i} x1={pad.l} x2={W - pad.r} y1={y(v)} y2={y(v)} stroke="rgb(var(--chart-grid))" strokeWidth="1" />
      ))}

      {/* overlay: shaded bands sit under the candles */}
      {overlay?.bands?.map((b, bi) => {
        const d = bandPath(b.upper, b.lower, cx, y);
        return d ? <path key={`band-${bi}`} d={d} fill={b.fill} stroke="none" /> : null;
      })}

      {candles.map((k, i) => {
        const rise = k.c >= k.o;
        const color = rise ? UP : DOWN;
        const bodyTop = Math.min(y(k.o), y(k.c));
        // A doji (open == close) would be a zero-height rect -> force 1px.
        const bodyH = Math.max(1, Math.abs(y(k.c) - y(k.o)));
        return (
          <g key={k.t} opacity={k.closed ? 1 : 0.8}>
            <line x1={cx(i)} x2={cx(i)} y1={y(k.h)} y2={y(k.l)} stroke={color} strokeWidth={bw > 6 ? 1.5 : 1} />
            <rect x={cx(i) - bw / 2} y={bodyTop} width={bw} height={bodyH} fill={color} />
            {/* Zoomed in far enough that each bar can show its own open/close ticks */}
            {bw >= 12 && (
              <>
                <line x1={cx(i) - bw / 2 - 3} x2={cx(i) - bw / 2} y1={y(k.o)} y2={y(k.o)} stroke={color} strokeWidth="1.5" />
                <line x1={cx(i) + bw / 2} x2={cx(i) + bw / 2 + 3} y1={y(k.c)} y2={y(k.c)} stroke={color} strokeWidth="1.5" />
              </>
            )}
          </g>
        );
      })}

      {/* overlay: moving-average / band / trailing lines drawn over the candles */}
      {overlay?.series?.map((s) =>
        segments(s.values, cx, y).map((pts, si) => (
          <polyline
            key={`${s.id}-${si}`}
            points={pts.join(" ")}
            fill="none"
            stroke={s.color}
            strokeWidth={s.width || 1.25}
            strokeDasharray={s.dash || undefined}
            strokeLinejoin="round"
            opacity="0.95"
          />
        ))
      )}

      {/* overlay: horizontal price lines (limit orders, grid rungs, my avg cost…) */}
      {overlay?.priceLines?.map((line, li) =>
        Number.isFinite(line.price) ? (
          <line
            key={`pl-${li}`}
            x1={pad.l}
            x2={W - pad.r}
            y1={y(line.price)}
            y2={y(line.price)}
            stroke={line.color}
            strokeWidth="1.25"
            strokeDasharray={line.dash || undefined}
            opacity="0.9"
          />
        ) : null
      )}

      {/* overlay: buy/sell signal triangles anchored to the bar's low/high */}
      {overlay?.markers?.map((m, mi) => {
        const k = candles[m.index];
        if (!k) return null;
        const x = cx(m.index);
        if (m.kind === "buy") {
          const yy = y(k.l) + 9;
          return <path key={`mk-${mi}`} d={`M ${x} ${yy - 7} L ${x + 4} ${yy} L ${x - 4} ${yy} Z`} fill={UP} />;
        }
        const yy = y(k.h) - 9;
        return <path key={`mk-${mi}`} d={`M ${x} ${yy + 7} L ${x + 4} ${yy} L ${x - 4} ${yy} Z`} fill={DOWN} />;
      })}

      {/* crosshair on the inspected bar */}
      {hover != null && candles[hover] && (
        <g pointerEvents="none">
          <line x1={cx(hover)} x2={cx(hover)} y1={pad.t} y2={H - pad.b} stroke="rgb(var(--chart-crosshair))" strokeWidth="1" strokeDasharray="2 3" opacity="0.8" />
          <line x1={pad.l} x2={W - pad.r} y1={y(candles[hover].c)} y2={y(candles[hover].c)} stroke="rgb(var(--chart-crosshair))" strokeWidth="1" strokeDasharray="2 3" opacity="0.55" />
        </g>
      )}

      {/* Current price is written at full UI size in the heading/readout. */}
      <line x1={pad.l} x2={W - pad.r} y1={y(last.c)} y2={y(last.c)} stroke={up ? UP : DOWN} strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />

      {/* right-edge tags for the labeled price lines */}
      {priceTags.map((t, ti) => (
        <g key={`tag-${ti}`} pointerEvents="none">
          <text
            x={W - pad.r - 3}
            y={t.ty - 2}
            textAnchor="end"
            className="num"
            style={{ fontSize: "9px", fontWeight: 700, fill: t.color, paintOrder: "stroke", stroke: "rgb(var(--c-surface))", strokeWidth: 3 }}
          >
            {t.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

// Small standalone oscillator pane (RSI) drawn under the main chart.
function RsiPane({ values, entry, exit }) {
  const W = 720;
  const H = 70;
  const pad = { l: 6, r: 6, t: 6, b: 6 };
  const n = values.length;
  const slot = (W - pad.l - pad.r) / n;
  const cx = (i) => pad.l + slot * (i + 0.5);
  const y = (v) => pad.t + (1 - v / 100) * (H - pad.t - pad.b);
  const pts = [];
  let cur = [];
  for (let i = 0; i < n; i++) {
    if (values[i] == null) {
      if (cur.length > 1) pts.push(cur);
      cur = [];
    } else cur.push(`${cx(i)},${y(values[i])}`);
  }
  if (cur.length > 1) pts.push(cur);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto mt-1" role="img" aria-label="RSI 보조 지표">
      {/* 과매수/과매도 영역 음영 */}
      <rect x={pad.l} y={pad.t} width={W - pad.l - pad.r} height={y(exit) - pad.t} fill="rgba(200,30,51,0.06)" />
      <rect x={pad.l} y={y(entry)} width={W - pad.l - pad.r} height={H - pad.b - y(entry)} fill="rgba(0,119,56,0.06)" />
      <line x1={pad.l} x2={W - pad.r} y1={y(exit)} y2={y(exit)} stroke={DOWN} strokeWidth="1" strokeDasharray="4 3" opacity="0.8" />
      <line x1={pad.l} x2={W - pad.r} y1={y(entry)} y2={y(entry)} stroke={UP} strokeWidth="1" strokeDasharray="4 3" opacity="0.8" />
      {pts.map((seg, si) => (
        <polyline key={si} points={seg.join(" ")} fill="none" stroke="rgb(99 102 241)" strokeWidth="1.5" strokeLinejoin="round" />
      ))}
      <text x={pad.l + 2} y={y(exit) - 3} style={{ fontSize: "8px", fill: DOWN }} className="num">
        과매수 {exit}
      </text>
      <text x={pad.l + 2} y={y(entry) + 9} style={{ fontSize: "8px", fill: UP }} className="num">
        과매도 {entry}
      </text>
    </svg>
  );
}

export default function CandleChart({
  symbol,
  market = "spot",
  defaultInterval = "1m",
  interval: controlledInterval,
  onIntervalChange,
  onLoadState,
  onData,
  compact = false,
  expanded = false,
  minimal = false,
  overlay = null,
  title,
}) {
  const [localInterval, setLocalInterval] = useState(defaultInterval);
  const [candles, setCandles] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM); // bars visible
  const [anchor, setAnchor] = useState(null); // null = pinned to live edge
  const [hover, setHover] = useState(null);
  const timer = useRef(null);
  const liveTimer = useRef(null);
  const candlesRef = useRef(null);
  const loadStateRef = useRef(onLoadState);
  loadStateRef.current = onLoadState;
  const dataRef = useRef(onData);
  dataRef.current = onData;
  const interval = controlledInterval ?? localInterval;

  const changeInterval = (value) => {
    if (controlledInterval == null) setLocalInterval(value);
    onIntervalChange?.(value);
  };

  // --- polling loop ---
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    let refreshMs = 3000;

    async function load(showSpinner) {
      if (showSpinner) {
        setLoading(true);
        loadStateRef.current?.({ status: "loading", symbol, interval, error: "" });
      }
      try {
        const d = await api.candles(symbol, interval, BUFFER, market);
        if (!alive) return;
        const nextCandles = d.candles || [];
        candlesRef.current = nextCandles;
        setCandles(nextCandles);
        dataRef.current?.({
          symbol,
          market,
          interval,
          candles: nextCandles,
          serverTime: d.server_time,
          stale: !!d.stale,
        });
        setError("");
        loadStateRef.current?.({
          status: nextCandles.length > 0 ? "ready" : "error",
          symbol,
          interval,
          error: nextCandles.length > 0 ? "" : "표시할 시세가 없어요.",
        });
        if (d.refresh_seconds) refreshMs = Math.max(2000, d.refresh_seconds * 1000);
      } catch (e) {
        if (alive) {
          const message = String(e.message || e);
          setError(message);
          loadStateRef.current?.({ status: "error", symbol, interval, error: message });
        }
      } finally {
        if (alive && showSpinner) setLoading(false);
      }
    }

    candlesRef.current = null;
    setCandles(null);
    setAnchor(null); // a new symbol/interval always starts at the live edge
    setHover(null);
    load(true);
    const arm = () => {
      timer.current = window.setTimeout(async () => {
        await load(false);
        if (alive) arm();
      }, refreshMs);
    };
    arm();

    return () => {
      alive = false;
      clearTimeout(timer.current);
    };
  }, [symbol, interval, market]);

  // Keep the in-progress candle moving independently from the full-buffer
  // refresh. Failures here deliberately keep the last good chart on screen;
  // the history loop remains responsible for user-visible load errors.
  useEffect(() => {
    if (!symbol) return undefined;
    let alive = true;
    let refreshMs = 3000;

    const arm = () => {
      liveTimer.current = window.setTimeout(async () => {
        if (!document.hidden) {
          try {
            const d = await api.liveCandles(symbol, interval, market);
            if (!alive) return;
            const merged = mergeLiveCandles(candlesRef.current, d.candles || []);
            if (merged && merged !== candlesRef.current) {
              candlesRef.current = merged;
              setCandles(merged);
              dataRef.current?.({
                symbol,
                market,
                interval,
                candles: merged,
                serverTime: d.server_time,
                stale: !!d.stale,
              });
            }
            if (d.refresh_seconds) refreshMs = Math.max(2000, d.refresh_seconds * 1000);
          } catch (_) {
            // A transient live-edge failure must not blank an otherwise valid chart.
          }
        }
        if (alive) arm();
      }, refreshMs);
    };

    arm();
    return () => {
      alive = false;
      window.clearTimeout(liveTimer.current);
    };
  }, [symbol, interval, market]);

  const total = candles?.length || 0;
  const maxZoom = Math.max(MIN_ZOOM, total);
  // Full-buffer overlay so indicators (BB, MA…) have their warm-up history.
  const overlayFull = useMemo(
    () => (overlay && candles && candles.length ? overlay(candles) : null),
    [overlay, candles]
  );
  const window_ = useMemo(() => {
    if (!total) return { start: 0, end: 0, bars: [] };
    const z = Math.min(zoom, total);
    const end = anchor == null ? total : Math.max(z, Math.min(anchor, total));
    return { start: end - z, end, bars: candles.slice(end - z, end) };
  }, [candles, total, zoom, anchor]);
  const view = window_.bars;

  // Slice the overlay arrays to the visible window; remap marker indices.
  const viewOverlay = useMemo(() => {
    if (!overlayFull) return null;
    const { start, end } = window_;
    const sliceVals = (a) => (a ? a.slice(start, end) : a);
    return {
      priceLines: overlayFull.priceLines,
      series: overlayFull.series?.map((s) => ({ ...s, values: sliceVals(s.values) })),
      bands: overlayFull.bands?.map((b) => ({ ...b, upper: sliceVals(b.upper), lower: sliceVals(b.lower) })),
      rsi: overlayFull.rsi ? { ...overlayFull.rsi, values: sliceVals(overlayFull.rsi.values) } : null,
      markers: (overlayFull.markers || [])
        .map((m) => ({ ...m, index: m.index - start }))
        .filter((m) => m.index >= 0 && m.index < end - start),
    };
  }, [overlayFull, window_]);

  const live = anchor == null; // following the newest bar

  const applyZoom = useCallback(
    (next) => {
      const z = Math.max(MIN_ZOOM, Math.min(Math.round(next), maxZoom));
      setZoom(z);
      setHover(null);
      // Keep the right edge where it is; clamp so the window stays in range.
      setAnchor((a) => (a == null ? null : Math.max(z, Math.min(a, total))));
    },
    [maxZoom, total]
  );

  const pan = useCallback(
    (bars) => {
      setHover(null);
      setAnchor((a) => {
        const cur = a == null ? total : a;
        const next = cur + bars;
        if (next >= total) return null; // snapped back to live
        return Math.max(Math.min(zoom, total), next);
      });
    },
    [total, zoom]
  );

  // Wheel zoom must be a NON-passive native listener: React registers onWheel
  // as passive, so preventDefault() there is ignored and the page scrolls too.
  // While the pointer is over the plot, the wheel belongs to chart zoom only.
  // Browser/page zoom shortcuts remain untouched for accessibility.
  const plotRef = useRef(null);
  useEffect(() => {
    const el = plotRef.current;
    if (!el) return;
    const onWheel = (e) => {
      if (e.ctrlKey || e.metaKey || e.deltaY === 0) return;
      e.preventDefault();
      const zoomingIn = e.deltaY < 0;
      applyZoom(zoom * (zoomingIn ? 0.85 : 1.18));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoom, maxZoom, applyZoom]);

  const quote = quoteOf(symbol);
  const last = view.length ? view[view.length - 1] : null;
  const firstBar = view.length ? view[0] : null;
  const changePct = last && firstBar?.o ? ((last.c - firstBar.o) / firstBar.o) * 100 : 0;
  const up = changePct >= 0;
  const inspected = hover != null && view[hover] ? view[hover] : last;

  const btn = "btn btn-s btn-secondary w-9 px-0";

  // 차트도 카드에 담지 않는다 — 캔버스 위에 그리고 구획은 괘선으로만(§1-3).
  return (
    <div className={`candle-chart pt-4 border-t border-slate-200 ${minimal ? "is-minimal" : ""}`}>
      <div className="candle-chart-toolbar flex items-center justify-between flex-wrap gap-2 mb-2">
        <div className="candle-chart-market">
          <h3 className="candle-chart-symbol t-title text-slate-900"><span className="num">{title || symbol}</span></h3>
          {last && (
            <div className="candle-chart-price-row">
              <strong className="candle-chart-current num text-slate-900">{fmtPrice(last.c)}</strong>
              <span className="candle-chart-quote t-caption text-slate-500">{quote}</span>
              <span className={"candle-chart-change t-label font-bold num " + (up ? "text-green-600" : "text-red-600")}>
                {up ? "+" : ""}
                {changePct.toFixed(2)}%
              </span>
              {live && !last.closed && (
                <span className="candle-chart-live flex items-center gap-1 t-caption font-bold text-red-600">
                  <span className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse motion-reduce:animate-none" />
                  LIVE
                </span>
              )}
            </div>
          )}
        </div>

        <div className="candle-chart-controls flex items-center gap-2">
          {!compact ? (
            <>
              <button onClick={() => applyZoom(zoom * 1.35)} disabled={zoom >= maxZoom} className={btn} title="축소 (더 많은 봉)" aria-label="차트 축소">
                −
              </button>
              <span className="t-caption text-slate-700 num w-14 text-center">{Math.min(zoom, total)}봉</span>
              <button onClick={() => applyZoom(zoom * 0.7)} disabled={zoom <= MIN_ZOOM} className={btn} title="확대 (봉 자세히)" aria-label="차트 확대">
                +
              </button>
              {!live && (
                <button onClick={() => setAnchor(null)} className="btn btn-s btn-secondary" title="최신 봉으로 이동">
                  최신
                </button>
              )}
            </>
          ) : null}
          <select
            value={interval}
            aria-label="차트 봉 간격"
            onChange={(e) => changeInterval(e.target.value)}
            className="field field-sm w-auto"
          >
            {INTERVALS.map((o) => (
              <option key={o.value} value={o.value}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>

      {/* OHLC read-out: hovered bar, or the latest one when not hovering */}
      <div className="candle-chart-readout mb-2 min-h-[20px]">
        <BarReadout bar={inspected} />
      </div>

      {/* 보조지표 범례 */}
      {viewOverlay && overlayFull?.legend?.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
          {overlayFull.legend.map((item, i) => (
            <LegendItem key={i} item={item} />
          ))}
        </div>
      )}

      {error && (
        <div className="notice-warn py-6 t-small text-slate-700">
          차트를 불러오지 못했어요: {error}
        </div>
      )}

      {!error && !candles && (
        <div className="h-[200px] flex items-center justify-center t-small text-slate-500">
          {loading ? "차트 불러오는 중…" : "—"}
        </div>
      )}

      {!error && view.length > 0 && (
        <div ref={plotRef}>
          <Chart candles={view} symbol={symbol} hover={hover} setHover={setHover} onPan={pan} overlay={viewOverlay} expanded={expanded} />
          {viewOverlay?.rsi && (
            <RsiPane values={viewOverlay.rsi.values} entry={viewOverlay.rsi.entry} exit={viewOverlay.rsi.exit} />
          )}
        </div>
      )}

      {/* 초보자용 한 줄 설명 */}
      {!minimal && !compact && !error && view.length > 0 && overlayFull?.note && (
        <div className="mt-2 notice t-small text-slate-700">{overlayFull.note}</div>
      )}

      {minimal && !error && view.length > 0 ? (
        <div className="candle-chart-range num">
          <span>{fullTime(view[0].t)}</span>
          <span>{fullTime(view[view.length - 1].t)}</span>
        </div>
      ) : null}

      {!minimal && !compact && !error && view.length > 0 && (
        <div className="mt-2 space-y-1 t-caption text-slate-500">
          <div className="flex items-center justify-between gap-3 num text-slate-700">
            <span>{fullTime(view[0].t)}</span>
            <span>{fullTime(view[view.length - 1].t)}</span>
          </div>
          <div className="flex items-center justify-between flex-wrap gap-1">
          <span>휠·＋/− 확대 · 드래그로 이동 · 봉 위에서 시가·고가·저가·종가 확인</span>
          <span>
            {live ? "마지막 봉은 진행 중 (실시간 갱신)" : "과거 구간 보는 중"} · 바이낸스 공개 시세
          </span>
          </div>
          {overlayFull && (
            <div className="text-slate-400">보조지표는 학습을 돕는 참고 표시예요. 실제 체결·수익을 보장하지 않아요.</div>
          )}
        </div>
      )}
    </div>
  );
}
