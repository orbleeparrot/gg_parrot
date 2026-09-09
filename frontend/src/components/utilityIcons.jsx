// 상단바 오른쪽 묶음의 아이콘 — 한 목소리(선 1.8, 둥근 끝, 24 격자). 채움·선 색은 CSS 의 currentColor 가 정한다.
// 다른 라이브러리 아이콘과 섞지 않는다 — 선 굵기가 다르면 같은 줄에서 튄다.
const base = { viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" };

export function DownloadIcon() {
  return <svg {...base}><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5" /></svg>;
}
export function HelpIcon() {
  return <svg {...base}><circle cx="12" cy="12" r="9" /><path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.7.4-1 .9-1 1.7M12 17h.01" /></svg>;
}
export function MoonIcon() {
  return <svg {...base}><path d="M20 15.5A8 8 0 0 1 8.5 4a8 8 0 1 0 11.5 11.5Z" /></svg>;
}
export function SunIcon() {
  return <svg {...base}><circle cx="12" cy="12" r="4" /><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M18.7 5.3l-1.4 1.4M6.7 17.3l-1.4 1.4" /></svg>;
}
export function UserIcon() {
  return <svg {...base}><circle cx="12" cy="8" r="3.25" /><path d="M5.75 19c.8-3.2 2.88-4.8 6.25-4.8s5.45 1.6 6.25 4.8" /></svg>;
}
export function KeyIcon() {
  return <svg {...base}><path d="M14.5 4a5.5 5.5 0 1 0 3.9 9.4L21 16v3h-3v-2h-2v-2h-2l-1.6-1.6A5.5 5.5 0 0 0 14.5 4Z" /><circle cx="15.5" cy="9" r="1" /></svg>;
}
