// 관리자 차트 — React SVG. 선(추이) · 막대(+오른쪽 축 선) · 가로 막대(비율·순위) · 퍼널 · 도넛(+표) · 누적 막대 · 열지도 칸.
// 모든 차트에 축 제목·눈금·격자·끝값. viewBox 폭은 실제 그려지는 폭으로 잡는다(DESIGN 차트 규칙 —
// 960 을 620px 열에 욱여넣으면 11px 글자가 7px 로 찍힌다). 색은 CSS 변수(라이트/다크는 스타일시트가 바꾼다).
// 애니메이션 없음 — reduced-motion 을 따로 신경 쓸 게 없다.
import { useCallback, useRef, useState } from "react";
import {
  bucketHours, donutArcs, funnelLayout, gridValues, hBarLayout, nearestIndex, nearestSlot, polylinePoints,
  seriesMax, stackTotals, tickIndexes, yScale,
} from "../../lib/adminChartMath.js";
import { EMPTY_NOTE, fmtDateTick, fmtInt, fmtNum, fmtPct } from "../../lib/adminFormat.js";
import { AdminTable, EmptyNote } from "./AdminBlocks.jsx";

export const SERIES = {
  s1: "var(--adm-s1)", s2: "var(--adm-s2)", s3: "var(--adm-s3)", s4: "var(--adm-s4)", s5: "var(--adm-s5)",
  up: "var(--adm-up)", down: "var(--adm-down)", warn: "var(--adm-warn)",
};

export { bucketHours };

// 감싸는 div 의 실제 픽셀 폭. ResizeObserver 가 없으면(옛 브라우저·테스트) 기본 960.
// ref 는 콜백 — 차트가 처음엔 EmptyNote(감싸는 div 없음)로 그려졌다가 60초 폴링·기간 변경으로
// 데이터가 생기는 경우, 마운트 시점에 useEffect([]) 는 이미 지나가서 관찰자가 안 붙고 960 에 갇힌다.
function useChartWidth(fallback = 960) {
  const roRef = useRef(null);
  const [width, setWidth] = useState(fallback);
  const ref = useCallback((el) => {
    roRef.current?.disconnect();
    roRef.current = null;
    if (!el || typeof ResizeObserver === "undefined") return;
    const apply = () => { const w = Math.round(el.getBoundingClientRect().width); if (w > 0) setWidth(Math.max(280, w)); };
    apply();
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    roRef.current = ro;
  }, []);
  return [ref, width];
}

// 차트 글자(11px)의 대략 폭. 라틴·숫자·"·" 는 JetBrains Mono 6.8px, 그 밖(한글 등)은 11.5px 로 넉넉히.
function textWidth(text) {
  let w = 0;
  for (const ch of String(text)) w += ch.charCodeAt(0) > 0x2fff ? 11.5 : 6.8;
  return w;
}

// 포인터 위치를 viewBox 좌표로 — 호버 값 표시용.
function pointerX(event, W) {
  const rect = event.currentTarget.getBoundingClientRect();
  return rect.width > 0 ? ((event.clientX - rect.left) * W) / rect.width : 0;
}

export function Legend({ items }) {
  return (
    <div className="adm-legend" aria-hidden="true">
      {items.map((it) => (
        <span key={it.label}><b className={it.line ? "is-line" : ""} style={{ background: it.color }} />{it.label}</span>
      ))}
    </div>
  );
}

// 격자 · 왼쪽 눈금 · 축 · 축 제목.
function Frame({ W, H, L, R, T, B, yMax, step, yTitle, xTitle, unit = "", fmt = fmtNum }) {
  const innerW = W - L - R;
  const innerH = H - T - B;
  const y = (v) => T + innerH - (v / yMax) * innerH;
  return (
    <g>
      {gridValues(yMax, step).map((v) => (
        <g key={v}>
          <line className="adm-grid" x1={L} x2={W - R} y1={y(v)} y2={y(v)} />
          <text x={L - 8} y={y(v) + 4} textAnchor="end">{fmt(v)}{unit}</text>
        </g>
      ))}
      <line className="adm-axis" x1={L} x2={L} y1={T} y2={T + innerH} />
      <line className="adm-axis" x1={L} x2={W - R} y1={T + innerH} y2={T + innerH} />
      <text className="adm-axis-title" x={L + innerW / 2} y={H - 4} textAnchor="middle">{xTitle}</text>
      <text className="adm-axis-title" transform={`translate(12 ${T + innerH / 2}) rotate(-90)`} textAnchor="middle">{yTitle}</text>
    </g>
  );
}

function DateTicks({ days, T, innerH, x }) {
  return tickIndexes(days.length).map((i) => (
    <text key={i} x={x(i)} y={T + innerH + 16} textAnchor="middle">{fmtDateTick(days[i])}</text>
  ));
}

function HoverLabel({ W, T, bottom, x, lines }) {
  const anchorEnd = x > W * 0.6;
  return (
    <g className="adm-hover" pointerEvents="none">
      <line className="adm-hover-line" x1={x} x2={x} y1={T} y2={bottom} />
      {lines.map((line, i) => (
        <text key={i} x={anchorEnd ? x - 8 : x + 8} y={T + 12 + i * 14} textAnchor={anchorEnd ? "end" : "start"} className="adm-hover-text" style={line.color ? { fill: line.color, fontWeight: 700 } : undefined}>
          {line.text}
        </text>
      ))}
    </g>
  );
}

// 선 차트 — series: [{ label, data: number[], color, dashed? }], days: "YYYY-MM-DD"[]
export function LineChart({ series, days, yTitle, xTitle = "날짜 (KST)", unit = "", height = 220, digits = 1 }) {
  const [ref, W] = useChartWidth();
  const [hover, setHover] = useState(null);
  const n = days.length;
  const filled = series.map((s) => ({ ...s, data: Array.from({ length: n }, (_, i) => Number(s.data?.[i]) || 0) }));
  const H = height;
  const L = 60;
  const R = 16;
  const T = 18;
  const B = 40;
  const innerW = W - L - R;
  const innerH = H - T - B;
  const { step, yMax } = yScale(seriesMax(filled));
  const x = (i) => L + (n > 1 ? (i / (n - 1)) * innerW : innerW / 2);
  const y = (v) => T + innerH - (v / yMax) * innerH;
  const fmt = (v) => fmtNum(v, digits);
  const ticks = new Set(tickIndexes(n));
  if (n === 0 || filled.every((s) => s.data.every((v) => !(v > 0)))) return <EmptyNote />;
  return (
    <div ref={ref} className="adm-chart-wrap">
      <svg
        className="adm-chart" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={yTitle}
        onPointerMove={(e) => setHover(nearestIndex(pointerX(e, W), L, innerW, n))}
        onPointerLeave={() => setHover(null)}
      >
        <Frame W={W} H={H} L={L} R={R} T={T} B={B} yMax={yMax} step={step} yTitle={yTitle} xTitle={xTitle} unit={unit} fmt={fmt} />
        {filled.map((s) => (
          <g key={s.label}>
            <polyline
              points={polylinePoints(s.data, x, y)} fill="none" stroke={s.color} strokeWidth="2.2" strokeLinejoin="round"
              strokeDasharray={s.dashed ? "5 4" : undefined}
            />
            {s.data.map((v, i) => (ticks.has(i) && i !== n - 1 ? <circle key={i} cx={x(i)} cy={y(v)} r="3" fill={s.color} /> : null))}
            {/* 끝점 — 표면색 링을 두른 점 + 끝값. 곡선이 어디서 끝났는지가 결론이다. */}
            <circle className="adm-endpoint" cx={x(n - 1)} cy={y(s.data[n - 1])} r="4" fill={s.color} />
            <text x={x(n - 1) - 7} y={y(s.data[n - 1]) - 8} textAnchor="end" style={{ fill: s.color, fontWeight: 700 }}>{fmt(s.data[n - 1])}{unit}</text>
          </g>
        ))}
        <DateTicks days={days} T={T} innerH={innerH} x={x} />
        {hover != null ? (
          <HoverLabel W={W} T={T} bottom={T + innerH} x={x(hover)} lines={[{ text: fmtDateTick(days[hover]) }, ...filled.map((s) => ({ text: `${s.label} ${fmt(s.data[hover])}${unit}`, color: s.color }))]} />
        ) : null}
      </svg>
    </div>
  );
}

// 막대 차트(+오른쪽 축 점선) — bars: [{ label, data, color }], line?: { label, data, color }
export function BarChart({ bars, line = null, days, yTitle, xTitle = "날짜 (KST)", unit = "", lineTitle = "", lineUnit = "%", height = 220 }) {
  const [ref, W] = useChartWidth();
  const [hover, setHover] = useState(null);
  const n = days.length;
  const filled = bars.map((s) => ({ ...s, data: Array.from({ length: n }, (_, i) => Number(s.data?.[i]) || 0) }));
  const lineData = line ? Array.from({ length: n }, (_, i) => Number(line.data?.[i]) || 0) : null;
  const H = height;
  const L = 56;
  const R = line ? 56 : 16;
  const T = 18;
  const B = 40;
  const innerW = W - L - R;
  const innerH = H - T - B;
  const { step, yMax } = yScale(seriesMax(filled));
  const y = (v) => T + innerH - (v / yMax) * innerH;
  const slot = n > 0 ? innerW / n : innerW;
  const bw = Math.max(2, (slot - Math.min(6, slot * 0.4)) / Math.max(1, filled.length));
  const cx = (i) => L + i * slot + slot / 2;
  const lineScale = lineData ? yScale(Math.max(...lineData)) : null;
  const ly = (v) => T + innerH - (v / lineScale.yMax) * innerH;
  const empty = n === 0 || (filled.every((s) => s.data.every((v) => !(v > 0))) && (!lineData || lineData.every((v) => !(v > 0))));
  if (empty) return <EmptyNote />;
  return (
    <div ref={ref} className="adm-chart-wrap">
      <svg
        className="adm-chart" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={yTitle}
        onPointerMove={(e) => setHover(nearestSlot(pointerX(e, W), L, innerW, n))}
        onPointerLeave={() => setHover(null)}
      >
        <Frame W={W} H={H} L={L} R={R} T={T} B={B} yMax={yMax} step={step} yTitle={yTitle} xTitle={xTitle} unit={unit} fmt={fmtInt} />
        {filled.map((s, si) => s.data.map((v, i) => (
          <rect key={`${s.label}-${i}`} x={L + i * slot + (slot - bw * filled.length) / 2 + si * bw} y={y(v)} width={bw} height={Math.max(0, T + innerH - y(v))} fill={s.color} rx="1.5" />
        )))}
        {lineData ? (
          <g>
            {gridValues(lineScale.yMax, lineScale.step).map((v) => (
              <text key={v} x={W - R + 8} y={ly(v) + 4} textAnchor="start" style={{ fill: line.color }}>{fmtNum(v, 1)}{lineUnit}</text>
            ))}
            <text className="adm-axis-title" transform={`translate(${W - 8} ${T + innerH / 2}) rotate(90)`} textAnchor="middle" style={{ fill: line.color }}>{lineTitle}</text>
            <polyline points={polylinePoints(lineData, cx, ly)} fill="none" stroke={line.color} strokeWidth="2.2" strokeDasharray="5 4" />
            <circle className="adm-endpoint" cx={cx(n - 1)} cy={ly(lineData[n - 1])} r="4" fill={line.color} />
            <text x={cx(n - 1) - 7} y={ly(lineData[n - 1]) - 8} textAnchor="end" style={{ fill: line.color, fontWeight: 700 }}>{fmtNum(lineData[n - 1], 1)}{lineUnit}</text>
          </g>
        ) : null}
        <DateTicks days={days} T={T} innerH={innerH} x={cx} />
        {hover != null ? (
          <HoverLabel
            W={W} T={T} bottom={T + innerH} x={cx(hover)}
            lines={[
              { text: fmtDateTick(days[hover]) },
              ...filled.map((s) => ({ text: `${s.label} ${fmtInt(s.data[hover])}${unit}`, color: s.color })),
              ...(lineData ? [{ text: `${line.label} ${fmtNum(lineData[hover], 1)}${lineUnit}`, color: line.color }] : []),
            ]}
          />
        ) : null}
      </svg>
    </div>
  );
}

// 가로 막대 — rows: [{ label, value, extra?, color? }]. 값과 보조값(비율)을 막대 끝에 적는다.
export function HBarChart({ rows, unit = "", color = SERIES.s2, title = "" }) {
  const [ref, W] = useChartWidth();
  const layout = hBarLayout(W);
  const { narrow, L, rowH, valueGap } = layout;
  const T = 8;
  const H = T + rows.length * rowH + 8;
  const max = Math.max(0, ...rows.map((r) => Number(r.value) || 0));
  const texts = rows.map((r) => `${fmtInt(r.value)}${unit}${r.extra ? ` · ${r.extra}` : ""}`);
  // 오른쪽 여백은 가장 긴 값 글자가 들어갈 만큼 — 폭 비율(12%)로만 잡으면 2열(≈640px)에서 "1,234 · 45.2%" 끝이 잘린다.
  // 11px JetBrains Mono 한 글자 ≈ 6.6px, 한글(단위 "건")은 Pretendard 로 떨어져 ≈ 11px.
  const need = Math.ceil(Math.max(0, ...texts.map(textWidth))) + valueGap;
  const R = narrow ? 0 : Math.max(layout.R, need);
  const innerW = W - L - R;
  if (rows.length === 0 || !(max > 0)) return <EmptyNote />;
  return (
    <div ref={ref} className="adm-chart-wrap">
      <svg className="adm-chart" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={title}>
        {rows.map((r, i) => {
          const y = T + i * rowH;
          const w = Math.max(2, ((Number(r.value) || 0) / max) * innerW);
          const barY = narrow ? y + 20 : y + 6;
          const valueText = texts[i];
          return (
            <g key={r.label}>
              {narrow
                ? <text className="adm-lab" x={0} y={y + 13}>{r.label}</text>
                : <text className="adm-lab" x={L - 12} y={y + 19} textAnchor="end">{r.label}</text>}
              <rect x={L} y={barY} width={w} height="18" fill={r.color || color} rx="2" />
              {narrow && w > innerW - 90
                ? <text x={L + w - valueGap} y={barY + 13} textAnchor="end" className="adm-on-bar">{valueText}</text>
                : <text x={L + w + valueGap} y={barY + 13}>{valueText}</text>}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// 퍼널 — steps: [{ label, count }]. 단계 간 전환율 + 첫 단계 대비.
export function FunnelChart({ steps }) {
  const [ref, W] = useChartWidth();
  const { narrow, L, R, rowH } = funnelLayout(W);
  const T = 8;
  const H = T + steps.length * rowH + 8;
  const first = Number(steps[0]?.count) || 0;
  const innerW = W - L - R;
  if (steps.length === 0 || !(first > 0)) return <EmptyNote />;
  return (
    <div ref={ref} className="adm-chart-wrap">
      <svg className="adm-chart" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label="전환 퍼널">
        {steps.map((s, i) => {
          const y = T + i * rowH;
          const value = Number(s.count) || 0;
          const w = Math.max(4, (value / first) * innerW);
          const prevCount = Number(steps[i - 1]?.count) || 0;
          const prev = i === 0 ? "—" : prevCount > 0 ? fmtPct((value / prevCount) * 100) : "—";
          const fromFirst = fmtPct((value / first) * 100);
          const barY = narrow ? y + 22 : y + 8;
          const inside = w > 70;
          return (
            <g key={s.label}>
              {narrow ? (
                <>
                  <text className="adm-lab" x={0} y={y + 14}>{i + 1}. {s.label}</text>
                  <text x={W} y={y + 14} textAnchor="end">이전 대비 {prev} · 방문 대비 {fromFirst}</text>
                </>
              ) : (
                <text className="adm-lab" x={L - 12} y={y + 23} textAnchor="end">{i + 1}. {s.label}</text>
              )}
              <rect x={L} y={barY} width={w} height="22" fill={SERIES.s2} opacity={1 - i * 0.09} rx="2" />
              {inside
                ? <text x={L + w - 8} y={barY + 15} textAnchor="end" className="adm-on-bar">{fmtInt(value)}</text>
                : <text x={L + w + 8} y={barY + 15} className="adm-strong">{fmtInt(value)}</text>}
              {!narrow ? (
                <>
                  <text x={W - R + 120} y={y + 23} textAnchor="end">이전 대비 {prev}</text>
                  <text x={W} y={y + 23} textAnchor="end">방문 대비 {fromFirst}</text>
                </>
              ) : null}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// 도넛 + 옆 표 — parts: [{ label, value, color, extra?: {key: text} }]. 가운데는 합계.
export function Donut({ parts, size = 180, unit = "", prefix = "", digits = 0, extraColumns = [], empty = EMPTY_NOTE }) {
  const r = 62;
  const cx = size / 2;
  const cy = size / 2;
  const { total, arcs } = donutArcs(parts, { cx, cy, r });
  if (!(total > 0)) return <EmptyNote>{empty}</EmptyNote>;
  const fmtValue = (v) => `${prefix}${fmtNum(v, digits)}${unit}`;
  const columns = [
    { key: "label", label: "구분", render: (row) => <><b className="adm-swatch" style={{ background: row.color }} />{row.label}</> },
    { key: "value", label: "값", num: true, render: (row) => fmtValue(row.value) },
    { key: "share_pct", label: "비율", num: true, render: (row) => fmtPct(row.share_pct) },
    ...extraColumns,
  ];
  return (
    <div className="adm-donut">
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="img" aria-label="구성비">
        {arcs.map((a) => <path key={a.label} d={a.d} fill="none" stroke={a.color} strokeWidth="26" />)}
        <text x={cx} y={cy + 5} textAnchor="middle" className="adm-donut-total num">{fmtValue(total)}</text>
      </svg>
      <AdminTable columns={columns} rows={arcs} rowKey={(row) => row.label} />
    </div>
  );
}

// 누적 막대(범주형 x) — cats: string[], series: [{ label, data, color }]. 막대 위에 합계.
export function StackedChart({ cats, series, yTitle, xTitle, unit = "", prefix = "", height = 220, digits = 0 }) {
  const [ref, W] = useChartWidth();
  const [hover, setHover] = useState(null);
  const n = cats.length;
  const filled = series.map((s) => ({ ...s, data: Array.from({ length: n }, (_, i) => Number(s.data?.[i]) || 0) }));
  const totals = stackTotals(filled, n);
  const H = height;
  const L = 64;
  const R = 16;
  const T = 18;
  const B = 40;
  const innerW = W - L - R;
  const innerH = H - T - B;
  const { step, yMax } = yScale(Math.max(0, ...totals));
  const y = (v) => T + innerH - (v / yMax) * innerH;
  const slot = n > 0 ? innerW / n : innerW;
  const bw = Math.min(46, Math.max(4, slot - 8));
  const fmt = (v) => `${prefix}${fmtNum(v, digits)}`;
  const labelEvery = n > 0 && slot < 34 ? Math.ceil(34 / slot) : 1;
  if (n === 0 || totals.every((v) => !(v > 0))) return <EmptyNote />;
  return (
    <div ref={ref} className="adm-chart-wrap">
      <svg
        className="adm-chart" viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={yTitle}
        onPointerMove={(e) => setHover(nearestSlot(pointerX(e, W), L, innerW, n))}
        onPointerLeave={() => setHover(null)}
      >
        <Frame W={W} H={H} L={L} R={R} T={T} B={B} yMax={yMax} step={step} yTitle={yTitle} xTitle={xTitle} unit={unit} fmt={fmt} />
        {cats.map((c, i) => {
          let acc = 0;
          const x = L + i * slot + (slot - bw) / 2;
          return (
            <g key={c}>
              {filled.map((s) => {
                const v = s.data[i];
                const rect = <rect key={s.label} x={x} y={y(acc + v)} width={bw} height={Math.max(0, y(acc) - y(acc + v))} fill={s.color} />;
                acc += v;
                return rect;
              })}
              {(i % labelEvery === 0 || i === n - 1) ? (
                <>
                  {totals[i] > 0 ? <text x={x + bw / 2} y={y(acc) - 5} textAnchor="middle" className="adm-strong">{fmt(acc)}</text> : null}
                  <text x={x + bw / 2} y={T + innerH + 16} textAnchor="middle">{c}</text>
                </>
              ) : null}
            </g>
          );
        })}
        {hover != null ? (
          <HoverLabel W={W} T={T} bottom={T + innerH} x={L + hover * slot + slot / 2} lines={[{ text: cats[hover] }, ...filled.map((s) => ({ text: `${s.label} ${fmt(s.data[hover])}${unit}`, color: s.color }))]} />
        ) : null}
      </svg>
    </div>
  );
}

// 열지도 칸(td) — 값이 클수록 진하게. null 은 "아직 지나지 않음".
export function HeatCell({ value, max = 70 }) {
  if (value === null || value === undefined) return <td className="num adm-muted">—</td>;
  const ratio = Math.min(1, Math.max(0, (Number(value) || 0) / max));
  return <td className="num adm-heat" style={{ "--heat-a": ratio.toFixed(2) }}>{fmtPct(value, 0)}</td>;
}
