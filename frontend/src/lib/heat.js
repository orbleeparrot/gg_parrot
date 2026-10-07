// 열 지도(익·손절 최적화) 칸 색 — 손익을 최대 크기로 나눈 -1…+1 을 CSS 변수 --heat 로 넘기고,
// 색은 index.css 의 .heat-cell 이 칠한다. 0(본전)은 무채색이어야 한다(DESIGN.md 차트 공통 규칙:
// 발산 스케일은 두 색 + 무채색 가운데, 기준점은 의미의 0).
export function heatLevel(value, extent) {
  if (!(extent > 0) || !Number.isFinite(Number(value))) return 0;
  return Number(Math.max(-1, Math.min(1, Number(value) / extent)).toFixed(3));
}
