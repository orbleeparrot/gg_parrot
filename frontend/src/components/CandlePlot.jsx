import { forwardRef, useImperativeHandle, useLayoutEffect, useRef } from "react";
import { CandlestickSeries, LineSeries, createChart, CrosshairMode, LineStyle, PriceLineSource } from "lightweight-charts";
import { fmtPrice } from "../lib/format.js";
import { StrategyPrimitive, resolveChartColor, RSI_SCALE_MARGINS } from "../lib/chartStrategyPrimitive.js";
import { candleData, canUpdateCandleData, sameCandleData, constrainCandleRange, restoreCandleRange, overlayPriceRange, priceMinMove } from "../lib/candleChartData.js";
import "./CandlePlot.css";

const fullTime = (time) => {
  const date = new Date(Number(time) * 1000);
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
};

function palette(element) {
  const resolve = (value) => resolveChartColor(value, element);
  return {
    resolve,
    fontFamily: getComputedStyle(element).fontFamily,
    up: resolve("rgb(var(--chart-up))"), down: resolve("rgb(var(--chart-down))"),
    upFaded: resolve("rgb(var(--chart-up) / .8)"), downFaded: resolve("rgb(var(--chart-down) / .8)"),
    text: resolve("rgb(var(--c-slate-900))"), muted: resolve("rgb(var(--chart-axis))"),
    surface: resolve("rgb(var(--c-surface))"), grid: resolve("rgb(var(--chart-grid))"),
    crosshair: resolve("rgb(var(--chart-crosshair))"), tag: resolve("rgb(var(--chart-crosshair-tag))"),
    rsi: resolve("rgb(var(--c-indigo-500))"),
  };
}

const CandlePlot = forwardRef(function CandlePlot({ candles, overlay, symbol, expanded, studio, onWindowChange, onHover }, ref) {
  const host = useRef(null);
  const canvas = useRef(null);
  const engine = useRef(null);
  const callbacks = useRef({ onWindowChange, onHover });
  callbacks.current = { onWindowChange, onHover };

  useImperativeHandle(ref, () => ({
    setZoom(count) { engine.current?.zoom(count); },
    goLive() { engine.current?.goLive(); },
  }), []);

  useLayoutEffect(() => {
    const colors = palette(host.current);
    const chart = createChart(canvas.current, {
      autoSize: true,
      layout: { background: { type: "solid", color: "transparent" }, textColor: colors.muted, fontSize: 11, fontFamily: getComputedStyle(host.current).fontFamily, attributionLogo: true, panes: { enableResize: false, separatorColor: colors.grid } },
      grid: { vertLines: { visible: false }, horzLines: { color: colors.grid } },
      crosshair: { mode: CrosshairMode.Magnet, vertLine: { color: colors.crosshair, labelBackgroundColor: colors.tag }, horzLine: { color: colors.crosshair, labelBackgroundColor: colors.tag } },
      rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.08, bottom: 0.08 }, minimumWidth: 64 },
      timeScale: {
        borderVisible: false, timeVisible: true, secondsVisible: false,
        // Our half-bar bounds keep both end candles fully visible. Native
        // fixRightEdge rounds that bound to the candle centre, adding a bar.
        fixLeftEdge: false, fixRightEdge: false, lockVisibleTimeRangeOnResize: true,
        shiftVisibleRangeOnNewBar: false,
        minBarSpacing: 0.001,
        tickMarkFormatter: (time, kind) => {
          const d = new Date(Number(time) * 1000);
          return kind < 3 ? `${d.getMonth() + 1}/${d.getDate()}` : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
        },
      },
      localization: { locale: "ko-KR", timeFormatter: fullTime },
      handleScroll: { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { mouseWheel: false, pinch: true, axisPressedMouseMove: { time: true, price: false }, axisDoubleClickReset: { time: true, price: false } },
    });
    const state = { chart, rows: [], overlay: null, colors, data: [], live: true, range: null, changing: false, hover: null, keyboard: false, disposed: false };
    const series = chart.addSeries(CandlestickSeries, {
      upColor: colors.up, downColor: colors.down, wickUpColor: colors.up, wickDownColor: colors.down,
      borderVisible: false, priceLineSource: PriceLineSource.LastVisible, priceLineStyle: LineStyle.Dashed,
      priceFormat: { type: "custom", formatter: fmtPrice, minMove: 0.00000001 },
      autoscaleInfoProvider: (original) => overlayPriceRange(original(), state.rows, state.overlay, chart.timeScale().getVisibleLogicalRange()),
    });
    const drawing = new StrategyPrimitive({ candles: [], overlay: null, colors });
    series.attachPrimitive(drawing);
    Object.assign(state, { series, drawing });

    const notifyRange = (range = chart.timeScale().getVisibleLogicalRange()) => {
      if (!range || !state.rows.length || state.disposed) return;
      state.range = range;
      state.live = range.to >= state.rows.length - 1;
      const start = Math.max(0, Math.ceil(range.from));
      const end = Math.min(state.rows.length, Math.floor(range.to) + 1);
      callbacks.current.onWindowChange?.({ start, end, live: state.live });
    };
    const setRange = (range) => {
      if (!state.rows.length) return;
      const bounded = constrainCandleRange(range, state.rows.length);
      // LWC applies a requested range on its next paint. Keep the requested
      // range immediately: another poll can arrive before that paint, and
      // reading the old native range here would undo timestamp anchoring.
      const current = chart.timeScale().getVisibleLogicalRange();
      state.pendingRange = current && Math.abs(current.from - bounded.from) < 0.05 && Math.abs(current.to - bounded.to) < 0.05 ? null : bounded;
      state.changing = true;
      chart.timeScale().setVisibleLogicalRange(bounded);
      state.changing = false;
      notifyRange(bounded);
    };
    const onRange = (range) => {
      if (!range || state.changing || !state.rows.length) return;
      if (state.pendingRange) {
        if (Math.abs(state.pendingRange.from - range.from) > 0.05 || Math.abs(state.pendingRange.to - range.to) > 0.05) return;
        state.pendingRange = null;
      }
      const bounded = constrainCandleRange(range, state.rows.length);
      if (Math.abs(bounded.from - range.from) > 0.05 || Math.abs(bounded.to - range.to) > 0.05) setRange(bounded);
      else notifyRange(range);
    };
    const clearHover = () => {
      state.hover = null;
      callbacks.current.onHover?.(null);
      chart.clearCrosshairPosition();
    };
    const zoom = (count) => {
      const range = state.range;
      if (!range) return;
      clearHover();
      setRange({ from: range.to - Math.round(count), to: range.to });
    };
    const goLive = () => {
      const span = state.range ? state.range.to - state.range.from : 80;
      clearHover();
      setRange({ from: state.rows.length - 0.5 - span, to: state.rows.length - 0.5 });
    };
    const onCrosshair = (event) => {
      if (state.keyboard) return;
      const index = event.time == null ? -1 : state.rows.findIndex((bar) => bar.t / 1000 === event.time);
      state.hover = index < 0 ? null : index;
      callbacks.current.onHover?.(state.hover);
    };
    const onKey = (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key) || !state.rows.length) return;
      event.preventDefault();
      if (event.key === "Escape") { clearHover(); return; }
      const first = Math.max(0, Math.ceil(state.range?.from ?? 0));
      const last = Math.min(state.rows.length - 1, Math.floor(state.range?.to ?? state.rows.length - 1));
      const index = event.key === "Home" ? first : event.key === "End" ? last : Math.max(first, Math.min(last, (state.hover ?? last) + (event.key === "ArrowLeft" ? -1 : 1)));
      const bar = state.rows[index];
      state.keyboard = true;
      chart.setCrosshairPosition(bar.c, bar.t / 1000, series);
      state.keyboard = false;
      state.hover = index;
      callbacks.current.onHover?.(index);
    };
    const onWheel = (event) => {
      if (event.ctrlKey || event.metaKey || !event.deltaY) return;
      event.preventDefault();
      const range = state.range;
      if (range) zoom((range.to - range.from) * (event.deltaY < 0 ? 0.85 : 1.18));
    };
    const onPointerDown = (event) => { if (!event.target.closest("a")) host.current?.focus({ preventScroll: true }); };
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    chart.subscribeCrosshairMove(onCrosshair);
    const element = host.current;
    element.addEventListener("keydown", onKey);
    element.addEventListener("wheel", onWheel, { passive: false });
    element.addEventListener("pointerdown", onPointerDown);
    Object.assign(state, { setRange, zoom, goLive, notifyRange });
    engine.current = state;

    const updateTheme = () => {
      if (state.disposed) return;
      const next = palette(element);
      state.colors = next;
      chart.applyOptions({
        layout: { textColor: next.muted, panes: { separatorColor: next.grid } },
        grid: { horzLines: { color: next.grid } },
        crosshair: { vertLine: { color: next.crosshair, labelBackgroundColor: next.tag }, horzLine: { color: next.crosshair, labelBackgroundColor: next.tag } },
      });
      series.applyOptions({ upColor: next.up, downColor: next.down, wickUpColor: next.up, wickDownColor: next.down });
      state.changing = true;
      state.data = candleData(state.rows, next);
      series.setData(state.data);
      state.changing = false;
      drawing.setData({ candles: state.rows, overlay: state.overlay, colors: next });
      state.rsiDrawing?.setData({ candles: state.rows, overlay: state.overlay, colors: next });
    };
    const themeObserver = new MutationObserver(updateTheme);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style", "data-theme"] });
    return () => {
      state.disposed = true;
      themeObserver.disconnect();
      element.removeEventListener("keydown", onKey);
      element.removeEventListener("wheel", onWheel);
      element.removeEventListener("pointerdown", onPointerDown);
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      chart.unsubscribeCrosshairMove(onCrosshair);
      chart.remove();
      engine.current = null;
    };
  }, [studio]);

  useLayoutEffect(() => {
    const state = engine.current;
    if (!state || !candles.length) return;
    const { chart, series } = state;
    const range = restoreCandleRange(state.range, state.rows, candles, state.live);
    const hoveredTime = state.hover == null ? null : state.rows[state.hover]?.t;
    const next = candleData(candles, state.colors);
    // Most polls only alter the newest one or two candles. Historical corrections
    // and a rolling buffer reset data on the existing chart, never recreate it.
    const canUpdate = canUpdateCandleData(state.data, next);
    state.changing = true;
    state.rows = candles;
    state.overlay = overlay;
    series.applyOptions({ priceFormat: { type: "custom", formatter: fmtPrice, minMove: priceMinMove(candles.at(-1).c) } });
    if (canUpdate) {
      for (let i = Math.max(0, state.data.length - 2); i < next.length; i++) {
        if (!sameCandleData(state.data[i], next[i])) series.update(next[i], i < state.data.length - 1);
      }
    } else series.setData(next);
    state.data = next;
    state.drawing.setData({ candles, overlay, colors: state.colors });
    if (overlay?.rsi) {
      if (!state.rsiSeries) {
        state.rsiSeries = chart.addSeries(LineSeries, {
          visible: true, color: "transparent", lineVisible: false, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false,
          priceFormat: { type: "price", precision: 1, minMove: 0.1 },
          autoscaleInfoProvider: () => ({ priceRange: { minValue: 0, maxValue: 100 } }),
        }, 1);
        state.rsiSeries.priceScale().applyOptions({ scaleMargins: RSI_SCALE_MARGINS, autoScale: true });
        state.rsiDrawing = new StrategyPrimitive({ candles, overlay, colors: state.colors, kind: "rsi" });
        state.rsiSeries.attachPrimitive(state.rsiDrawing);
        chart.panes()[1].setHeight(110);
      }
      // Warm-up gaps are real whitespace, not invented RSI values. The primitive
      // retains threshold zones even before the first RSI can be calculated.
      state.rsiSeries.setData(candles.map((bar, i) => overlay.rsi.values[i] == null
        ? { time: bar.t / 1000 } : { time: bar.t / 1000, value: overlay.rsi.values[i] }));
      state.rsiDrawing.setData({ candles, overlay, colors: state.colors });
    } else if (state.rsiSeries) {
      chart.removeSeries(state.rsiSeries);
      state.rsiSeries = null;
      state.rsiDrawing = null;
    }
    state.changing = false;
    state.setRange(range);
    if (hoveredTime != null) {
      const index = candles.findIndex((bar) => bar.t === hoveredTime);
      state.hover = index < 0 ? null : index;
      callbacks.current.onHover?.(state.hover);
    }
  }, [candles, overlay, studio]);

  return (
    <div ref={host} className={`financial-candle-plot${expanded ? " is-expanded" : ""}${overlay?.rsi ? " has-rsi" : ""}`} tabIndex={0} role="group" aria-label={`${symbol} 캔들 차트. 방향키로 봉 정보 확인, Home·End로 처음·마지막 봉, Escape로 해제`}>
      <div ref={canvas} className="financial-candle-canvas" />
      {overlay?.rsi && <span className="sr-only">RSI 보조지표. 과매수 {overlay.rsi.exit} · {overlay.rsi.highLabel}, 과매도 {overlay.rsi.entry} · {overlay.rsi.lowLabel}</span>}
    </div>
  );
});

export default CandlePlot;
