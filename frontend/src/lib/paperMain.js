// 페이퍼 트레이딩 주 버튼 하나 — 상태에 따라 문구만 바뀐다: 시작 → 중지 → 다시 시작. 폭은 CSS 로 고정(.sd-paper-main).
export function paperMainButton({ running, hasSession, busy }) {
  if (running) return { label: busy ? "중지 중…" : "중지", action: "stop", tone: "danger" };
  return { label: busy ? "시작 중…" : hasSession ? "다시 시작" : "시작", action: "start", tone: "primary" };
}
