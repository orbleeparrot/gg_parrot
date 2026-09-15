// 캐러셀 점 표시 — 기사가 수십 개여도 점은 최대 max 개만, 현재 기사를 가운데 두고 잘라 보여 준다.
// 점이 늘어나며 그리드 열을 밀어 카드가 열 밖으로 나가던 문제를 막는다(개수는 옆의 n / N 이 말해 준다).
export function visibleDotIndexes(active, length, max = 11) {
  const total = Math.max(0, Math.floor(Number(length) || 0));
  const limit = Math.max(1, Math.floor(Number(max) || 1));
  if (total <= limit) return Array.from({ length: total }, (_, i) => i);
  const current = ((Math.floor(Number(active) || 0) % total) + total) % total;
  let start = current - Math.floor(limit / 2);
  start = Math.max(0, Math.min(start, total - limit));
  return Array.from({ length: limit }, (_, i) => start + i);
}
