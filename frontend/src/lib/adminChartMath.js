// 관리자 차트의 순수 계산 — 눈금 간격·축 범위·x 눈금 자리·도넛 호·시간대 묶기.
// SVG 를 그리는 컴포넌트(components/admin/AdminCharts.jsx)와 떼어 둔 이유: node --test 는 JSX 를
// 읽지 못하므로, 눈으로 확인하기 어려운 수학은 여기서 단위 테스트로 잡는다.

// 최댓값을 4칸쯤으로 나누는 '보기 좋은' 간격(1·2·5·10 × 10^n).
export function niceStep(max) {
  const raw = Math.max(Number(max) || 0, 1) / 4;
  const p = 10 ** Math.floor(Math.log10(raw));
  const m = raw / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

// 축 상단은 간격의 배수로 올림. 전부 0 이어도 0~step 한 칸은 그린다(축이 사라지지 않게).
// minStep: 건수처럼 정수로 찍는 축은 1 을 준다 — 최댓값이 1·2 면 niceStep 이 0.5 를 골라
// 정수 라벨이 "0, 1, 1, 2, 2" 로 겹쳐 찍히기 때문(작은 사이트에선 흔한 경우).
export function yScale(max, { minStep = 0 } = {}) {
  const step = Math.max(Number(minStep) || 0, niceStep(max));
  const top = Math.ceil((Number(max) || 0) / step - 1e-9) * step;
  return { step, yMax: Math.max(step, +top.toFixed(6)) };
}

export function gridValues(yMax, step) {
  const out = [];
  for (let v = 0; v <= yMax + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

// 날짜 눈금은 7일이면 매일, 30일이면 5일마다, 90일이면 15일마다 — 글자가 겹치지 않는 밀도.
export function tickEvery(n) {
  if (n <= 10) return 1;
  if (n <= 31) return 5;
  if (n <= 62) return 10;
  return 15;
}

// 눈금 index 목록. 마지막 날짜는 항상 찍되, 직전 눈금과 너무 붙으면 직전 것을 뺀다.
export function tickIndexes(n) {
  if (!(n > 0)) return [];
  const k = tickEvery(n);
  const out = [];
  for (let i = 0; i < n; i += k) out.push(i);
  const last = out[out.length - 1];
  if (last !== n - 1) {
    if (n - 1 - last < k / 2) out.pop();
    out.push(n - 1);
  }
  return out;
}

export function seriesMax(seriesList) {
  let max = 0;
  for (const s of seriesList || []) for (const v of s?.data || []) if (Number.isFinite(v) && v > max) max = v;
  return max;
}

export function stackTotals(seriesList, n) {
  const totals = new Array(n).fill(0);
  for (const s of seriesList || []) for (let i = 0; i < n; i += 1) totals[i] += Number(s?.data?.[i]) || 0;
  return totals;
}

export function polylinePoints(values, x, y) {
  return values.map((v, i) => `${x(i).toFixed(1)},${y(Number(v) || 0).toFixed(1)}`).join(" ");
}

function finiteOrNull(v) {
  if (v === null || v === undefined || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

// 선을 null 에서 끊는다 — 측정 시작일 이전 구간(서버가 null)을 0 으로 그리면 "그날 전환율 0%" 라는 가짜 결론이 된다.
// 점 하나짜리 구간은 선이 안 보이므로 single 로 표시해 점으로 찍는다.
export function polylineSegments(values, x, y) {
  const segments = [];
  let run = [];
  (values || []).forEach((v, i) => {
    const n = finiteOrNull(v);
    if (n === null) {
      if (run.length) segments.push(run);
      run = [];
      return;
    }
    run.push({ i, v: n });
  });
  if (run.length) segments.push(run);
  return segments.map((pts) => ({
    points: pts.map((p) => `${x(p.i).toFixed(1)},${y(p.v).toFixed(1)}`).join(" "),
    single: pts.length === 1,
    first: pts[0],
    last: pts[pts.length - 1],
  }));
}

// 마지막으로 값이 있는 index — 끝값 라벨은 여기 찍는다(오늘이 null 이면 어제 값이 끝값). 없으면 -1.
export function lastFiniteIndex(values) {
  for (let i = (values || []).length - 1; i >= 0; i -= 1) if (finiteOrNull(values[i]) !== null) return i;
  return -1;
}

// 퍼널 행 — 막대 폭은 innerW 를 넘지 않고(8,640% 가 화면 밖으로 나가 숫자가 안 보이던 문제), 뒤 단계가 앞 단계보다
// 크면 집합이 어긋난 자료라 비율은 null("—"). 서버가 pct_of_first/pct_of_prev 키를 준 행은 그 값을 그대로 쓴다
// (null 이면 null — 서버가 "측정 불가"라고 한 것을 여기서 나눠 지어내지 않는다). 키가 없을 때만 count 로 계산.
export function funnelRows(steps, innerW) {
  const list = Array.isArray(steps) ? steps : [];
  const first = Math.max(0, Number(list[0]?.count) || 0);
  const width = Math.max(0, Number(innerW) || 0);
  return list.map((s, i) => {
    const count = Math.max(0, Number(s?.count) || 0);
    const prev = i === 0 ? null : Math.max(0, Number(list[i - 1]?.count) || 0);
    const overFirst = count > first;
    const overPrev = prev !== null && count > prev;
    const w = first > 0 ? Math.min(width, Math.max(4, (count / first) * width)) : 4;
    const serverFirst = s && typeof s === "object" && "pct_of_first" in s;
    const serverPrev = s && typeof s === "object" && "pct_of_prev" in s;
    let pctFirst = serverFirst ? finiteOrNull(s.pct_of_first) : (first > 0 ? (count / first) * 100 : null);
    let pctPrev = serverPrev ? finiteOrNull(s.pct_of_prev) : (prev > 0 ? (count / prev) * 100 : null);
    if (i === 0) pctPrev = null;
    if (overFirst) pctFirst = null;
    if (overFirst || overPrev) pctPrev = null;
    return { key: s?.key ?? String(i), label: s?.label || s?.key || `${i + 1}`, count, width: w, pctFirst, pctPrev };
  });
}

// 포인터 x(px, viewBox 좌표) → 가장 가까운 점 index (선 차트) / 칸 index (막대 차트).
export function nearestIndex(px, left, innerW, n) {
  if (!(n > 1) || !(innerW > 0)) return 0;
  const t = (px - left) / innerW;
  return Math.min(n - 1, Math.max(0, Math.round(t * (n - 1))));
}

export function nearestSlot(px, left, innerW, n) {
  if (!(n > 0) || !(innerW > 0)) return 0;
  const t = (px - left) / innerW;
  return Math.min(n - 1, Math.max(0, Math.floor(t * n)));
}

// 도넛 호 경로. 값이 하나뿐(=100%)이면 SVG 호가 사라지므로 반원 둘로 그린다. 합이 0 이면 호 없음.
export function donutArcs(parts, { cx, cy, r }) {
  const clean = (parts || []).map((p) => ({ ...p, value: Math.max(0, Number(p?.value) || 0) }));
  const total = clean.reduce((acc, p) => acc + p.value, 0);
  if (!(total > 0)) return { total: 0, arcs: [] };
  let angle = -Math.PI / 2;
  const point = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const arcs = clean.map((p) => {
    const sweep = (p.value / total) * Math.PI * 2;
    const [x1, y1] = point(angle);
    let d;
    if (sweep >= Math.PI * 2 - 1e-6) {
      const [xm, ym] = point(angle + Math.PI);
      d = `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 1 1 ${xm.toFixed(2)} ${ym.toFixed(2)} A${r} ${r} 0 1 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
    } else {
      const [x2, y2] = point(angle + sweep);
      d = sweep > 0
        ? `M${x1.toFixed(2)} ${y1.toFixed(2)} A${r} ${r} 0 ${sweep > Math.PI ? 1 : 0} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`
        : "";
    }
    angle += sweep;
    return { ...p, d, share_pct: (p.value / total) * 100 };
  });
  return { total, arcs };
}

// 시간대별 행(hour 0..23)을 size 시간 단위로 묶는다. 빠진 시간은 0.
export function bucketHours(hourly, size = 2, keys = ["items", "failures"]) {
  const buckets = [];
  for (let start = 0; start < 24; start += size) {
    const row = { hour: start, label: `${String(start).padStart(2, "0")}시` };
    for (const k of keys) row[k] = 0;
    buckets.push(row);
  }
  for (const h of hourly || []) {
    const hour = Number(h?.hour);
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) continue;
    const row = buckets[Math.floor(hour / size)];
    for (const k of keys) row[k] += Number(h?.[k]) || 0;
  }
  return buckets;
}

// 좁은 폭(모바일)에서는 가로 막대의 라벨이 막대 위로 올라간다 — 왼쪽 여백 비율이 아니라 절대폭이 문제라서.
export function hBarLayout(W, { rowH = 30, narrowRowH = 44, narrowBelow = 640 } = {}) {
  const narrow = W < narrowBelow;
  if (narrow) return { narrow, L: 0, R: 0, rowH: narrowRowH, valueGap: 6 };
  return { narrow, L: Math.min(200, Math.round(W * 0.22)), R: Math.min(110, Math.round(W * 0.12)), rowH, valueGap: 8 };
}

export function funnelLayout(W, { rowH = 38, narrowRowH = 58, narrowBelow = 720 } = {}) {
  const narrow = W < narrowBelow;
  if (narrow) return { narrow, L: 0, R: 0, rowH: narrowRowH };
  return { narrow, L: Math.min(250, Math.round(W * 0.26)), R: Math.min(290, Math.round(W * 0.3)), rowH };
}
