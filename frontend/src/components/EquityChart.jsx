import { useEffect, useMemo, useRef, useState } from "react";
import { AreaSeries, CrosshairMode, LineStyle, createChart } from "lightweight-charts";
import "./EquityChart.css";

const DEFAULT_H = 240;

const compact = (v) => {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toFixed(2);
};

// Backtest sampling and the previous chart both space samples by their order.
// Ordinal chart keys keep every point, including repeated dates; visible dates
// always come from the original curve, never from these internal keys.
const seriesData = (curve) => curve.map((point, index) => ({ time: index + 1, value: point.equity }));

class Endpoint {
  attached({ chart, series, requestUpdate }) {
    Object.assign(this, { chart, series, requestUpdate });
  }
  update(point, color, surface) {
    Object.assign(this, { point, color, surface });
    this.requestUpdate?.();
  }
  paneViews() {
    return [{ zOrder: () => "top", renderer: () => ({ draw: (target) => {
      if (!this.point) return;
      const x = this.chart.timeScale().timeToCoordinate(this.point.time);
      const y = this.series.priceToCoordinate(this.point.value);
      if (x == null || y == null) return;
      target.useMediaCoordinateSpace(({ context }) => {
        context.beginPath();
        context.arc(x, y, 4, 0, Math.PI * 2);
        context.fillStyle = this.color;
        context.fill();
        context.strokeStyle = this.surface;
        context.lineWidth = 2;
        context.stroke();
      });
    } }) }];
  }
}

function EquityPlot({ curve, height, stretch }) {
  const hostRef = useRef(null);
  const chartRef = useRef(null);
  const currentRef = useRef(null);
  const hoverRef = useRef(null);
  const keyboardRef = useRef(false);
  const [hover, setHover] = useState(null);
  const data = useMemo(() => seriesData(curve), [curve]);
  const start = curve[0].equity;
  const end = curve[curve.length - 1].equity;
  const up = end >= start;
  currentRef.current = { curve, data, up };

  useEffect(() => {
    const host = hostRef.current;
    const chart = createChart(host, {
      autoSize: true,
      layout: { background: { type: "solid", color: "transparent" }, attributionLogo: true },
      grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      leftPriceScale: { visible: false },
      rightPriceScale: { visible: false, scaleMargins: { top: 0.08, bottom: 0.12 } },
      timeScale: { visible: false, rightOffset: 0, minBarSpacing: 0.001, lockVisibleTimeRangeOnResize: true },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { labelVisible: false, style: LineStyle.SparseDotted, width: 1 },
        horzLine: { visible: false, labelVisible: false },
      },
      // Keep the complete backtest visible and let mobile swipes and mouse
      // wheels continue scrolling the surrounding result page.
      handleScroll: false,
      handleScale: false,
      kineticScroll: { mouse: false, touch: false },
      localization: { priceFormatter: compact },
    });
    const series = chart.addSeries(AreaSeries, {
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerRadius: 4,
      crosshairMarkerBorderWidth: 2,
    });
    const baseline = series.createPriceLine({
      price: currentRef.current.curve[0].equity,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
      title: "",
    });
    const endpoint = new Endpoint();
    series.attachPrimitive(endpoint);
    const applyTheme = () => {
      const style = getComputedStyle(host);
      const color = (token, fallback, alpha = 1) => {
        const triplet = style.getPropertyValue(token).trim() || fallback;
        return `rgba(${triplet.split(/\s+/).join(",")},${alpha})`;
      };
      const stroke = color(currentRef.current.up ? "--chart-up" : "--chart-down", currentRef.current.up ? "0 192 135" : "246 70 93");
      const fill = color(currentRef.current.up ? "--chart-up" : "--chart-down", currentRef.current.up ? "0 192 135" : "246 70 93", 0.12);
      const surface = color("--c-surface", "255 255 255");
      chart.applyOptions({
        layout: { textColor: color("--chart-axis", "106 116 128"), fontFamily: style.fontFamily },
        crosshair: { vertLine: { color: color("--chart-crosshair", "100 116 139", 0.8) } },
      });
      series.applyOptions({ lineColor: stroke, topColor: fill, bottomColor: fill, crosshairMarkerBackgroundColor: stroke, crosshairMarkerBorderColor: surface });
      baseline.applyOptions({ color: color("--chart-axis", "106 116 128", 0.5) });
      endpoint.update(currentRef.current.data.at(-1), stroke, surface);
    };
    const onCrosshair = (event) => {
      if (keyboardRef.current) return;
      const index = typeof event.time === "number" ? event.time - 1 : null;
      const valid = index != null && index >= 0 && index < currentRef.current.curve.length && event.point;
      const next = valid ? { index, x: event.point.x } : null;
      hoverRef.current = next;
      setHover(next);
    };
    const fitCurve = () => {
      const count = currentRef.current.data.length;
      const width = host.clientWidth;
      if (!width) return;
      // A 1,000-point mobile curve must remain complete. Leave enough physical
      // space for the endpoint ring even when each sample is less than 1px wide.
      const spacing = Math.max(0.001, (width - 16) / Math.max(1, count - 1));
      const margin = Math.max(0, 8 / spacing - 0.5);
      chart.timeScale().setVisibleLogicalRange({ from: -margin, to: count - 1 + margin });
    };
    chart.subscribeCrosshairMove(onCrosshair);
    // Hidden time axes report width 0; observe the actual plot instead.
    const resizeObserver = new ResizeObserver(fitCurve);
    resizeObserver.observe(host);
    const themeObserver = new MutationObserver(applyTheme);
    themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "style"] });
    chartRef.current = { chart, series, baseline, applyTheme, fitCurve };
    applyTheme();
    return () => {
      themeObserver.disconnect();
      resizeObserver.disconnect();
      chart.unsubscribeCrosshairMove(onCrosshair);
      chartRef.current = null;
      chart.remove();
    };
  }, []);

  useEffect(() => {
    const active = chartRef.current;
    if (!active) return;
    active.series.setData(data);
    active.baseline.applyOptions({ price: start });
    active.applyTheme();
    active.fitCurve();
    active.chart.clearCrosshairPosition();
    keyboardRef.current = false;
    hoverRef.current = null;
    setHover(null);
  }, [data, start]);

  const clearHover = () => {
    keyboardRef.current = false;
    chartRef.current?.chart.clearCrosshairPosition();
    hoverRef.current = null;
    setHover(null);
  };
  const onKeyDown = (event) => {
    if (event.key === "Escape") { clearHover(); return; }
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const current = hoverRef.current?.index ?? curve.length - 1;
    const index = event.key === "Home" ? 0 : event.key === "End" ? curve.length - 1
      : Math.max(0, Math.min(curve.length - 1, current + (event.key === "ArrowRight" ? 1 : -1)));
    const active = chartRef.current;
    if (!active) return;
    keyboardRef.current = true;
    active.chart.setCrosshairPosition(curve[index].equity, data[index].time, active.series);
    const next = { index, x: active.chart.timeScale().timeToCoordinate(data[index].time) ?? 0 };
    hoverRef.current = next;
    setHover(next);
  };
  const at = hover ? curve[hover.index] : null;

  return (
    <div className={"equity-chart w-full relative" + (stretch ? " equity-fill" : "")}>
      <div
        className={"equity-chart-plot touch-pan-y" + (stretch ? " equity-fill-svg" : "")}
        style={stretch ? undefined : { aspectRatio: `720 / ${height}` }}
        role="img"
        aria-label={`자산곡선. 시작 ${compact(start)}, 최종 ${compact(end)}. 좌우 화살표로 날짜별 금액 확인`}
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerMoveCapture={() => { keyboardRef.current = false; }}
        onPointerLeave={clearHover}
        onBlur={clearHover}
      >
        <div ref={hostRef} className="equity-chart-canvas" />
        {at && (
          <div
            className="equity-chart-tooltip t-caption num text-slate-900 bg-surface border border-slate-200 rounded-lg px-2 py-1 shadow-lg"
            style={{ left: hover.x, transform: `translateX(${hover.index >= curve.length / 2 ? "-100%" : "0"})` }}
          >
            {at.t.slice(0, 10)} · {compact(at.equity)}
          </div>
        )}
      </div>
      <div className="equity-caption flex justify-between gap-4 t-caption text-slate-700 num mt-1">
        <span>{curve[0].t.slice(0, 10)} <span className="text-slate-500">시작 {compact(start)}</span></span>
        <span className={up ? "text-green-600" : "text-red-600"}>
          <span className="text-slate-500">{curve[curve.length - 1].t.slice(0, 10)} 최종</span> {compact(end)}
        </span>
      </div>
    </div>
  );
}

export default function EquityChart({ curve, height = DEFAULT_H, stretch = false }) {
  if (!curve || curve.length < 2) {
    return <div className="t-small text-slate-500">자산곡선 데이터가 없어요.</div>;
  }
  return <EquityPlot curve={curve} height={height} stretch={stretch} />;
}
