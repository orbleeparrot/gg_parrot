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

export function ThumbUpIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.3a2 2 0 0 0 2-1.7l1.4-9a2 2 0 0 0-2-2.3H14zM7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3" />
    </svg>
  );
}

export function ThumbDownIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.7a2 2 0 0 0-2 1.7l-1.4 9a2 2 0 0 0 2 2.3H10zM17 2h2.7a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H17" />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
    </svg>
  );
}

