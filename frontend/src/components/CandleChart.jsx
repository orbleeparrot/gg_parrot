import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api.js";
import { fmtPrice, quoteOf } from "../lib/format.js";
import { exchangeLabel, isDomestic, normalizeExchange } from "../lib/exchanges.js";
import { applyChartHistory, applyChartLive, chartTimeKst, createChartStream, isChartFresh, isChartLive } from "../lib/chartSource.js";
import CandlePlot from "./CandlePlot.jsx";
import "./CandleChartStudio.css";

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

const INTERVALS = [
  { value: "1m", label: "1분" },
  { value: "5m", label: "5분" },
  { value: "15m", label: "15분" },
  { value: "1h", label: "1시간" },
  { value: "4h", label: "4시간" },
  { value: "1d", label: "1일" },
];

// --- inspector panel: OHLC of the hovered (or latest) bar ---------------
function BarReadout({ bar, live, quote, extra = null }) {
  if (!bar) return null;
  const rise = bar.c >= bar.o;
  const pct = bar.o ? ((bar.c - bar.o) / bar.o) * 100 : 0;
  const cell = (label, v) => (
    <span className="candle-ohlc-cell whitespace-nowrap">
      <span className="text-slate-500">{label}</span>{" "}
      <span className="num font-semibold text-slate-900">{fmtPrice(v, quote)}</span>
    </span>
  );
  return (
    <div className="candle-readout-row">
      <div className="candle-ohlc flex flex-wrap items-center gap-x-3 gap-y-1 t-caption">
        <span className="candle-ohlc-time text-slate-700 num">{chartTimeKst(bar.t)} KST</span>
        {cell("시", bar.o)}
        {cell("고", bar.h)}
        {cell("저", bar.l)}
        {cell("종", bar.c)}
        <span className={"candle-ohlc-change font-bold num " + (rise ? "text-green-600" : "text-red-600")}>
          {rise ? "+" : ""}
          {pct.toFixed(2)}%
        </span>
      </div>
      {extra}
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
          className={"candle-legend-line" + (item.dash ? " is-dashed" : "")}
          style={{ "--swatch": item.color }}
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
        <strong className="candle-chart-current num text-slate-900">{fmtPrice(bar.c, quote)}</strong>
        <span className="candle-chart-quote t-caption text-slate-500">{quote}</span>
      </span>
      <RangeChange percent={changePct} />
    </div>
  );
}

function SourceStatus({ feed, now, inline = false }) {
  const { source, history, live, historyError, liveError, mismatch } = feed;
  const status = (meta, error, edge = false) => error ? "오류 · 마지막 시세 유지" : !meta ? "확인 대기" :
    meta.stale ? "지연 · 캐시 시세" : meta.awaiting ? "진행봉 갱신 대기" :
      !meta.fetchedAt ? "원천 수집 시각 확인 대기" : edge && !isChartFresh(meta, now) ? "갱신 대기" : meta.cached ? "캐시" : "확인";
  const time = (meta) => meta?.fetchedAt ? ` · ${chartTimeKst(meta.fetchedAt)} KST` : "";
  const warning = [
    mismatch && "시장 변경 감지 · 전체 시세 재확인",
    (liveError || !isChartFresh(live, now)) && `실시간: ${status(live, liveError, true)}`,
    (historyError || history?.stale) && `과거봉: ${status(history, historyError)}`,
  ].filter(Boolean).join(" · ");
  // inline — 직접 만들기 차트의 OHLC 줄 끝에 붙는다. 거래소 이름은 차트 제목에 이미 있으니 빼고,
  // 국내는 현물뿐이라 시장도 뺀다(2026-10-02, 두 줄 중복 정리).
  const market = source?.market === "futures" ? "선물" : "현물";
  const label = !source ? "시세 출처 확인 중 · 시세 정보"
    : inline ? (isDomestic(source.exchange) ? "시세 정보" : `${market} · 시세 정보`)
      : `${exchangeLabel(source.exchange)} ${market} · 시세 정보`;
  return (
    <details className={"candle-source t-caption text-slate-500" + (inline ? " is-inline" : "")}>
      <summary className="cursor-pointer" title={inline ? [label, source?.fallback && "선물 요청 → 현물 대체", warning].filter(Boolean).join(" · ") : undefined}>
        {label}
        {source?.fallback && <span className="text-amber-700"> · 선물 요청 → 현물 대체</span>}
        {warning && <span className="text-amber-700" role="status" aria-live="polite"> · {warning}</span>}
      </summary>
      <div className="pt-1">
        <span>KST (UTC+9) · 수집 시각은 봉 시작 시각과 달라요.</span>
        <span> · 과거봉: {status(history, historyError)}{time(history)}</span>
        <span> · 실시간: {status(live, liveError, true)}{time(live)}</span>
      </div>
    </details>
  );
}

export default function CandleChart({
  symbol,
  exchange: exchangeValue = "binance",
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
  const exchange = normalizeExchange(exchangeValue);
  const studio = variant === "studio";
  const disabledIntervalMap = Object.fromEntries((disabledIntervals || []).map((item) => [item.value, item.title || "이 테스트 기간에서는 고를 수 없어요"]));
  const [localInterval, setLocalInterval] = useState(defaultInterval);
  const [candles, setCandles] = useState(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [feed, setFeed] = useState({ source: null, history: null, live: null, historyError: "", liveError: "", mismatch: false });
  const [now, setNow] = useState(Date.now);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM); // bars visible
  const [anchor, setAnchor] = useState(null); // null = pinned to live edge
  const [hover, setHover] = useState(null);
  const timer = useRef(null);
  const liveTimer = useRef(null);
  const loadStateRef = useRef(onLoadState);
  loadStateRef.current = onLoadState;
  const dataRef = useRef(onData);
  dataRef.current = onData;
  const interval = controlledInterval ?? localInterval;
  const intervalLocked = controlledInterval != null && !onIntervalChange;

  const changeInterval = (value) => {
    if (disabledIntervalMap[value]) return;
    if (controlledInterval == null) setLocalInterval(value);
    onIntervalChange?.(value);
  };

  // One source-aware stream owns both loops. Live polls follow the history's
  // actual market, never a requested futures market that fell back to spot.
  useEffect(() => {
    if (!symbol) return;
    let alive = true;
    let stream = createChartStream({ exchange, symbol, interval, market });
    let historyRequest = null;
    let liveRequest = null;
    let historyRefreshMs = 3000;
    let liveRefreshMs = 3000;
    let lastReloadAt = -Infinity;
    let historyError = "";
    let liveError = "";
    let mismatch = false;
    const report = () => {
      if (!alive) return;
      setFeed({ source: stream.source, history: stream.history, live: stream.live, historyError, liveError, mismatch });
      setNow(Date.now());
    };
    const publish = (payload) => {
      if (!alive) return;
      setCandles(stream.candles);
      report();
      if (stream.source) dataRef.current?.({
        exchange: stream.source.exchange, symbol: stream.source.symbol,
        market: stream.source.market, requestedMarket: market, interval,
        candles: stream.candles, serverTime: payload.server_time,
        fetchedAt: (stream.liveData || stream.history)?.fetchedAt,
        priceOrigin: (stream.liveData || stream.history)?.priceOrigin,
        stale: !!stream.history?.stale || !!stream.live?.stale,
        awaiting: !!stream.history?.awaiting || !!stream.live?.awaiting,
      });
    };
    function loadHistory(showSpinner) {
      if (!alive || historyRequest) return historyRequest;
      if (showSpinner) {
        setLoading(true);
        loadStateRef.current?.({ status: "loading", exchange, symbol, interval, error: "" });
      }
      historyRequest = (async () => {
        try {
          const d = await api.candles(symbol, interval, BUFFER, market, exchange);
          if (!alive) return;
          const previousMarket = stream.source?.market;
          stream = applyChartHistory(stream, d);
          historyError = "";
          mismatch = false;
          if (previousMarket !== stream.source.market) liveError = "";
          publish(d);
          setError("");
          loadStateRef.current?.({ status: stream.candles.length ? "ready" : "error", exchange, symbol, interval,
            market: stream.source.market, error: stream.candles.length ? "" : "표시할 시세가 없어요." });
          if (d.refresh_seconds) historyRefreshMs = Math.max(2000, d.refresh_seconds * 1000);
        } catch (e) {
          if (alive) {
            historyError = String(e.message || e);
            setError(historyError);
            report();
            loadStateRef.current?.({ status: "error", exchange, symbol, interval, error: historyError });
          }
        } finally {
          historyRequest = null;
          if (alive && showSpinner) setLoading(false);
        }
      })();
      return historyRequest;
    }
    const reloadHistory = () => {
      if (Date.now() - lastReloadAt < 15_000 || historyRequest) return;
      lastReloadAt = Date.now();
      void loadHistory(false);
    };
    function loadLive() {
      if (!alive || liveRequest || !stream.source || !stream.candles.length) return liveRequest;
      const revision = stream.revision;
      const actualMarket = stream.source.market;
      liveRequest = (async () => {
        try {
          const d = await api.liveCandles(symbol, interval, actualMarket, exchange);
          if (!alive || revision !== stream.revision) return;
          const accepted = applyChartLive(stream, d, revision);
          if (accepted.reload && accepted.stream === stream) {
            mismatch = true;
            report();
            reloadHistory();
            return;
          }
          stream = accepted.stream;
          mismatch = false;
          liveError = "";
          publish(d);
          if (accepted.reload) reloadHistory();
          if (d.refresh_seconds) liveRefreshMs = Math.max(2000, d.refresh_seconds * 1000);
        } catch (e) {
          if (alive && revision === stream.revision) {
            liveError = String(e.message || e);
            report();
          }
        } finally { liveRequest = null; }
      })();
      return liveRequest;
    }
    setError("");
    report();
    setCandles(null);
    setAnchor(null); // a new symbol/interval always starts at the live edge
    setHover(null);
    void loadHistory(true);
    const armHistory = () => {
      timer.current = window.setTimeout(async () => {
        // 백그라운드 탭은 아무도 보지 않는 화면에 300봉(~29 KB)을 계속 받아간다.
        // 라이브 루프에는 원래 있던 가드가 여기엔 빠져 있어서, 탭 하나만 열어둬도
        // 대역폭이 무한정 새어나갔다.
        if (!document.hidden) await loadHistory(false);
        if (alive) armHistory();
      }, historyRefreshMs);
    };
    const armLive = () => {
      liveTimer.current = window.setTimeout(async () => {
        if (!document.hidden) await loadLive();
        if (alive) armLive();
      }, liveRefreshMs);
    };
    armHistory();
    armLive();
    const onVisibility = () => {
      if (!document.hidden) { report(); void loadHistory(false); void loadLive(); }
    };
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      alive = false;
      clearTimeout(timer.current);
      clearTimeout(liveTimer.current);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [exchange, symbol, interval, market]);

  // Freshness expires even when an in-flight network request never completes.
  useEffect(() => {
    const tick = () => { if (!document.hidden) setNow(Date.now()); };
    const id = window.setInterval(tick, 3000);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(id); document.removeEventListener("visibilitychange", tick); };
  }, []);

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
  const current = candles?.at(-1) || null;
  const firstBar = view.length ? view[0] : null;
  const changePct = last && firstBar?.o ? ((last.c - firstBar.o) / firstBar.o) * 100 : 0;
  const inspected = hover != null && candles?.[hover] ? candles[hover] : last;
  const fresh = !feed.historyError && !feed.liveError && !feed.mismatch && !feed.history?.stale &&
    !feed.history?.awaiting && isChartLive(feed.live || feed.history, current, now);

  const btn = "btn btn-s btn-secondary w-9 px-0";

  if (studio) {
    const zoomBtn = "btn btn-s btn-secondary w-8 px-0";
    return (
      <div className="candle-chart is-studio">
        <div className="candle-chart-toolbar">
          <div className="candle-chart-market">
            <h3 className="candle-chart-symbol t-caption text-slate-500">{exchangeLabel(exchange)} · <span className="num">{title || symbol}</span></h3>
            <MarketPrice bar={current} quote={quote} changePct={changePct} />
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

        {view.length === 0 && <SourceStatus feed={feed} now={now} />}

        {error && <div className="notice-warn py-6 t-small text-slate-700">차트를 불러오지 못했어요: {error}</div>}
        {!error && (!candles || !candles.length) && (
          <div className="candle-chart-stage flex items-center justify-center t-small text-slate-500">{loading ? "차트 불러오는 중…" : candles ? "표시할 시세가 없어요." : "—"}</div>
        )}
        {view.length > 0 && (
          <div className="candle-chart-stage">
            <div className="candle-chart-readout"><BarReadout bar={inspected} quote={quote} live={live && fresh && !last.closed} extra={<SourceStatus feed={feed} now={now} inline />} /></div>
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
          <h3 className="candle-chart-symbol t-caption text-slate-500">{exchangeLabel(exchange)} · <span className="num">{title || symbol}</span></h3>
          <MarketPrice bar={current} quote={quote} changePct={changePct} />
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
            disabled={intervalLocked}
            title={intervalLocked ? "테스트한 봉 간격으로 고정되어 있어요" : undefined}
            onChange={(e) => changeInterval(e.target.value)}
            className="field field-sm w-auto"
          >
            {INTERVALS.map((o) => (
              <option key={o.value} value={o.value} disabled={!!disabledIntervalMap[o.value]} title={disabledIntervalMap[o.value]}>{o.label}</option>
            ))}
          </select>
        </div>
      </div>

      <SourceStatus feed={feed} now={now} />

      {/* OHLC read-out: hovered bar, or the latest one when not hovering */}
      <div className="candle-chart-readout">
        <BarReadout bar={inspected} quote={quote} live={live && fresh && last && !last.closed} />
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
        <div className="h-50 flex items-center justify-center t-small text-slate-500">
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
            <span>{exchangeLabel(exchange)} 공개 시세</span>
          </div>
          {overlayFull && (
            <div className="text-slate-400">보조지표는 학습을 돕는 참고 표시예요. 실제 체결·수익을 보장하지 않아요.</div>
          )}
        </div>
      )}
    </div>
  );
}
