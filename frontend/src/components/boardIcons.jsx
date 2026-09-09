// 게시판의 작은 아이콘 — 상단바 아이콘과 같은 목소리(선 1.8, 둥근 끝, 24 격자). 색은 currentColor.
const base = { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" };

export function ImageIcon() {
  return <svg {...base}><rect x="3.5" y="5" width="17" height="14" rx="2" /><circle cx="9" cy="10" r="1.6" /><path d="m20.5 15.5-4.5-4.5-6 6-2.5-2.5L3.5 19" /></svg>;
}
export function CommentIcon() {
  return <svg {...base}><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4h11A2.5 2.5 0 0 1 20 6.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 3.5V17H6.5A2.5 2.5 0 0 1 4 14.5v-8Z" /></svg>;
}
export function ChevronLeftIcon() {
  return <svg {...base}><path d="m15 6-6 6 6 6" /></svg>;
}
export function ChevronRightIcon() {
  return <svg {...base}><path d="m9 6 6 6-6 6" /></svg>;
}
