import { useCallback, useMemo, useRef, useState } from "react";

// Dependency-free SVG line chart of the equity curve (deterministic, offline-safe).
//
// 값을 읽는 통로가 툴팁 하나뿐이면 안 된다 — 그래서 시작·최종 금액과 날짜는 축
// 바깥에 직접 적고, 호버는 그 위에 얹는 보조 수단으로만 둔다. 초기자본선(본전)은
// 파선으로 긋는다: 격자가 아니라 임계선이라 파선이 의미를 갖는다.
const W = 720;
const DEFAULT_H = 240;
const PAD = { l: 8, r: 8, t: 14, b: 22 };

const compact = (v) => {
  const a = Math.abs(v);
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toFixed(2);
};

// height — viewBox 높이. 폭에 비례해 그려지므로 낮출수록 납작해진다.
// stretch — 부모가 준 높이를 그대로 채운다(비율 무시). 선·면뿐이라 세로로 늘려도 글자가 찌그러지지 않는다 —
//        직접 만들기 결과 독처럼 높이가 창에 따라 정해지는 칸에서 스크롤을 만들지 않으려고.
export default function EquityChart({ curve, height = DEFAULT_H, stretch = false }) {
  const H = height;
  const svgRef = useRef(null);
  const [hover, setHover] = useState(null);

  const n = curve?.length || 0;
  const geom = useMemo(() => {
    if (!curve || n < 2) return null;
    const values = curve.map((p) => p.equity);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || Math.abs(max) * 0.001 || 1;
    // 위아래로 숨통을 틔워 선이 테두리에 닿지 않게 한다.
    const lo = min - span * 0.08;
    const hi = max + span * 0.08;
    const range = hi - lo || 1;
    const x = (i) => PAD.l + (i / (n - 1)) * (W - PAD.l - PAD.r);
    const y = (v) => PAD.t + (1 - (v - lo) / range) * (H - PAD.t - PAD.b);
    const line = values.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    const area = `${line} L${x(n - 1).toFixed(1)},${H - PAD.b} L${x(0).toFixed(1)},${H - PAD.b} Z`;
    return { values, x, y, line, area, start: values[0], end: values[n - 1] };
  }, [curve, n, H]);

  const indexAt = useCallback(
    (clientX) => {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect || !rect.width) return null;
      const vx = ((clientX - rect.left) / rect.width) * W;
      const ratio = (vx - PAD.l) / (W - PAD.l - PAD.r);
      const i = Math.round(ratio * (n - 1));
      return i >= 0 && i < n ? i : null;
    },
    [n]
  );

  if (!curve || n < 2) {
    return <div className="t-small text-slate-500">자산곡선 데이터가 없어요.</div>;
  }

  const { values, x, y, line, area, start, end } = geom;
  const up = end >= start;
  // Theme-aware (see index.css): the vars hold bare "R G B" triplets, so the
  // same value serves both the solid stroke and the translucent area fill.
  const rgb = up ? "var(--chart-up)" : "var(--chart-down)";
  const stroke = `rgb(${rgb})`;
  const fill = `rgb(${rgb} / 0.12)`;

  const at = hover != null ? curve[hover] : null;
  const firstDay = curve[0].t.slice(0, 10);
  const lastDay = curve[n - 1].t.slice(0, 10);

  return (
    <div className={"w-full relative" + (stretch ? " equity-fill" : "")}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio={stretch ? "none" : undefined}
        className={(stretch ? "w-full equity-fill-svg" : "w-full h-auto") + " touch-pan-y"}
        role="img"
        aria-label={`자산곡선. 시작 ${compact(start)}, 최종 ${compact(end)}`}
        tabIndex={0}
        onPointerMove={(e) => setHover(indexAt(e.clientX))}
        onPointerLeave={() => setHover(null)}
        onKeyDown={(e) => {
          if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
          e.preventDefault();
          setHover((h) => {
            const cur = h == null ? n - 1 : h;
            return Math.max(0, Math.min(n - 1, cur + (e.key === "ArrowRight" ? 1 : -1)));
          });
        }}
      >
        {/* 초기자본선 — 이 선 위가 이익, 아래가 손실이라는 기준 */}
        <line
          x1={PAD.l} x2={W - PAD.r} y1={y(start)} y2={y(start)}
          stroke="rgb(var(--chart-axis))" strokeWidth="1" strokeDasharray="4 4" opacity="0.5"
        />
        <path d={area} fill={fill} />
        <path d={line} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />

        {/* 끝점을 강조 — 곡선이 어디서 끝났는지가 이 차트의 결론이다.
            겹치는 표면 위에서도 떨어져 보이도록 표면색 링을 두른다. */}
        <circle cx={x(n - 1)} cy={y(end)} r="4" fill={stroke} stroke="rgb(var(--c-surface))" strokeWidth="2" vectorEffect={stretch ? "non-scaling-stroke" : undefined} />

        {hover != null && (
          <g pointerEvents="none">
            <line
              x1={x(hover)} x2={x(hover)} y1={PAD.t} y2={H - PAD.b}
              stroke="rgb(var(--chart-crosshair))" strokeWidth="1" strokeDasharray="2 3" opacity="0.8"
            />
            <circle cx={x(hover)} cy={y(values[hover])} r="4" fill={stroke} stroke="rgb(var(--c-surface))" strokeWidth="2" />
          </g>
        )}
      </svg>

      {/* 호버 값 — 툴팁이 유일한 통로가 되지 않도록 아래 캡션에도 값을 남긴다 */}
      {at && (
        <div
          className="absolute -top-1 t-caption num text-slate-900 bg-surface border border-slate-200 rounded-lg px-2 py-1 shadow-lg pointer-events-none whitespace-nowrap"
          style={{
            left: `${(x(hover) / W) * 100}%`,
            transform: `translateX(${hover > n / 2 ? "-100%" : "0"})`,
          }}
        >
          {at.t.slice(0, 10)} · {compact(at.equity)}
        </div>
      )}

      {/* 차트 캡션은 좌우 끝에 13/600 (§4 자리별 적용표) */}
      <div className="equity-caption flex justify-between gap-4 t-caption text-slate-700 num mt-1">
        <span>
          {firstDay} <span className="text-slate-500">시작 {compact(start)}</span>
        </span>
        <span className={up ? "text-green-600" : "text-red-600"}>
          <span className="text-slate-500">{lastDay} 최종</span> {compact(end)}
        </span>
      </div>
    </div>
  );
}
