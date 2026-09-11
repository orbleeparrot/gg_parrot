import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtPrice, quoteOf } from "../lib/format.js";
import CandlePlot from "./CandlePlot.jsx";

// Market-data orchestration and chart controls. CandlePlot owns the renderer.
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

const pad2 = (n) => String(n).padStart(2, "0");

function fullTime(ms) {
  const d = new Date(ms);
  return `${d.getMonth() + 1}/${d.getDate()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
}

// --- inspector panel: OHLC of the hovered (or latest) bar ---------------
function BarReadout({ bar, live }) {
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
    <div className="candle-readout-row">
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
      </div>
      {live && (
        <span className="candle-chart-live inline-flex items-center gap-1.5 t-caption font-bold text-red-600">
          <span aria-hidden="true" className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse motion-reduce:animate-none" />
          LIVE
        </span>
      )}
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

function RangeChange({ percent }) {
  const up = percent >= 0;
  return (
    <span
      className="candle-chart-change inline-flex items-baseline gap-1.5 whitespace-nowrap"
      title="화면에 보이는 첫 봉의 시가 대비 마지막 봉의 종가"
    >
      <span className={"t-label font-bold num " + (up ? "text-green-600" : "text-red-600")}>
        {up ? "+" : ""}{percent.toFixed(2)}%
      </span>
      <span className="t-caption font-medium text-slate-500">구간 등락</span>
    </span>
  );
}

function MarketPrice({ bar, quote, changePct }) {
  if (!bar) return null;
  return (
    <div className="candle-chart-price-row">
      <span className="candle-chart-price">
        <strong className="candle-chart-current num text-slate-900">{fmtPrice(bar.c)}</strong>
        <span className="candle-chart-quote t-caption text-slate-500">{quote}</span>
      </span>
      <RangeChange percent={changePct} />
    </div>
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
  // "studio" — 직접 만들기 워크벤치용. 종목·시세와 차트 조작 아래 OHLC·LIVE를 표시하고,
  // 그림은 판의 남은 공간을 채운다.
  variant = "default",
  // 고를 수 없는 봉 간격(예: 테스트 기간에서 봉 수 한도를 넘는 것) — [{ value, title }]. 도구줄 segmented 에서 비활성.
  disabledIntervals = [],
}) {
  const studio = variant === "studio";
  const disabledIntervalMap = Object.fromEntries((disabledIntervals || []).map((item) => [item.value, item.title || "이 테스트 기간에서는 고를 수 없어요"]));
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
        // 백그라운드 탭은 아무도 보지 않는 화면에 300봉(~29 KB)을 계속 받아간다.
        // 라이브 루프에는 원래 있던 가드가 여기엔 빠져 있어서, 탭 하나만 열어둬도
        // 대역폭이 무한정 새어나갔다.
        if (!document.hidden) await load(false);
        if (alive) arm();
      }, refreshMs);
    };
    arm();

    // 돌아왔을 때 다음 폴링까지 최대 봉 간격만큼 기다리지 않도록 즉시 한 번 따라잡는다.
    const onVisibility = () => {
      if (!document.hidden) load(false);
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      alive = false;
      clearTimeout(timer.current);
      document.removeEventListener("visibilitychange", onVisibility);
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

  const live = anchor == null;
  const chartRef = useRef(null);
  const applyZoom = useCallback((next) => {
    chartRef.current?.setZoom(Math.max(MIN_ZOOM, Math.min(Math.round(next), maxZoom)));
  }, [maxZoom]);
  const onWindowChange = useCallback(({ start, end, live: followsLive }) => {
    setZoom(end - start);
    setAnchor(followsLive ? null : end);
  }, []);
  const goLive = () => chartRef.current?.goLive();
  const plot = <CandlePlot
    ref={chartRef} candles={candles || []} symbol={symbol} overlay={overlayFull}
    expanded={expanded} studio={studio} onWindowChange={onWindowChange} onHover={setHover}
  />;

  const quote = quoteOf(symbol);
  const last = view.length ? view[view.length - 1] : null;
  const firstBar = view.length ? view[0] : null;
  const changePct = last && firstBar?.o ? ((last.c - firstBar.o) / firstBar.o) * 100 : 0;
  const inspected = hover != null && candles?.[hover] ? candles[hover] : last;

  const btn = "btn btn-s btn-secondary w-9 px-0";

  if (studio) {
    const zoomBtn = "btn btn-s btn-secondary w-8 px-0";
    return (
      <div className="candle-chart is-studio">
        <div className="candle-chart-toolbar">
          <div className="candle-chart-market">
            <h3 className="candle-chart-symbol t-caption text-slate-500"><span className="num">{title || symbol}</span></h3>
            <MarketPrice bar={last} quote={quote} changePct={changePct} />
          </div>
          <div className="candle-chart-controls">
            {/* 봉 간격은 표시만 — 값은 왼쪽 조건에서 정한다. 한도를 넘는 간격은 흐리게. */}
            <div className="seg candle-chart-intervals" role="group" aria-label="봉 간격 (조건에서 정해요)" title="봉 간격은 왼쪽 조건에서 정해요">
              {INTERVALS.map((o) => (
                <span
                  key={o.value}
                  aria-current={interval === o.value ? "true" : undefined}
                  className={"seg-item " + (interval === o.value ? "seg-item-on" : "") + (disabledIntervalMap[o.value] ? " is-unavailable" : "")}
                >
                  {o.label}
                </span>
              ))}
            </div>
            <button onClick={() => applyZoom(zoom * 1.35)} disabled={zoom >= maxZoom} className={zoomBtn} title="축소 (더 많은 봉)" aria-label="차트 축소">−</button>
            <span className="t-caption text-slate-700 num candle-chart-zoom">{Math.min(zoom, total)}봉</span>
            <button onClick={() => applyZoom(zoom * 0.7)} disabled={zoom <= MIN_ZOOM} className={zoomBtn} title="확대 (봉 자세히)" aria-label="차트 확대">+</button>
            {!live && (
              <button onClick={goLive} className="btn btn-s btn-secondary" title="최신 봉으로 이동">최신</button>
            )}
          </div>
          {overlayFull?.legend?.length > 0 && (
            <div className="candle-chart-legend">
              {overlayFull.legend.map((item, i) => <LegendItem key={i} item={item} />)}
            </div>
          )}
        </div>

        {error && <div className="notice-warn py-6 t-small text-slate-700">차트를 불러오지 못했어요: {error}</div>}
        {!error && (!candles || !candles.length) && (
          <div className="candle-chart-stage flex items-center justify-center t-small text-slate-500">{loading ? "차트 불러오는 중…" : candles ? "표시할 시세가 없어요." : "—"}</div>
        )}
        {view.length > 0 && (
          <div className="candle-chart-stage">
            <div className="candle-chart-readout"><BarReadout bar={inspected} live={live && !last.closed} /></div>
            <div className="candle-chart-plot">
              {plot}
            </div>
          </div>
        )}
      </div>
    );
  }

  // 차트도 카드에 담지 않는다 — 캔버스 위에 그리고 구획은 괘선으로만(§1-3).
  return (
    <div className={`candle-chart pt-4 border-t border-slate-200 ${minimal ? "is-minimal" : ""}`}>
      <div className="candle-chart-toolbar flex items-center justify-between flex-wrap gap-2">
        <div className="candle-chart-market">
          <h3 className="candle-chart-symbol t-caption text-slate-500"><span className="num">{title || symbol}</span></h3>
          <MarketPrice bar={last} quote={quote} changePct={changePct} />
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
                <button onClick={goLive} className="btn btn-s btn-secondary" title="최신 봉으로 이동">
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
      <div className="candle-chart-readout">
        <BarReadout bar={inspected} live={live && last && !last.closed} />
      </div>

      {/* 보조지표 범례 */}
      {overlayFull?.legend?.length > 0 && (
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

      {!error && (!candles || !candles.length) && (
        <div className="h-[200px] flex items-center justify-center t-small text-slate-500">
          {loading ? "차트 불러오는 중…" : candles ? "표시할 시세가 없어요." : "—"}
        </div>
      )}

      {view.length > 0 && (
        <div>{plot}</div>
      )}

      {/* 초보자용 한 줄 설명 */}
      {!minimal && !compact && !error && view.length > 0 && overlayFull?.note && (
        <div className="mt-2 notice t-small text-slate-700">{overlayFull.note}</div>
      )}

      {!minimal && !compact && !error && view.length > 0 && (
        <div className="mt-2 space-y-1 t-caption text-slate-500">
          <div className="flex items-center justify-between flex-wrap gap-1">
            <span>휠·＋/− 확대 · 드래그로 이동 · 봉 위에서 시가·고가·저가·종가 확인</span>
            <span>바이낸스 공개 시세</span>
          </div>
          {overlayFull && (
            <div className="text-slate-400">보조지표는 학습을 돕는 참고 표시예요. 실제 체결·수익을 보장하지 않아요.</div>
          )}
        </div>
      )}
    </div>
  );
}
