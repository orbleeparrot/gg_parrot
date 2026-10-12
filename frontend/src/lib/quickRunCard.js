// 직접 만들기 → 빠른 실행으로 넘어갈 때, 그 매크로 카드(백테스트 결과까지)를 실행 가이드가 그대로 보이게 잠깐 들고 간다.
// 같은 탭의 화면 이동 사이에만 쓰는 메모리다 — 새로 고침하면 사라지고, 그때 가이드는 저장된 매크로 요약을 보인다.
// 백테스트 곡선이 커서 history.state 에는 싣지 않는다. 가장 최근 한 장만 둔다.
let latest = null;

export function rememberQuickRunCard(macroId, card) {
  if (macroId == null || !card?.macro) return;
  latest = { id: String(macroId), card };
}

export function quickRunCard(macroId) {
  if (macroId == null || !latest || latest.id !== String(macroId)) return null;
  return latest.card;
}

export function forgetQuickRunCard() {
  latest = null;
}
