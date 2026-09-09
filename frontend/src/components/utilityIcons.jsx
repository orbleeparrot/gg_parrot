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
  return <svg {...base}><circle cx="8" cy="8" r="5" /><path d="m11.5 11.5 9 9M16 16l3-3M19 19l3-3" /></svg>;
}
export function ChevronDownIcon() {
  return <svg {...base}><path d="m6 9 6 6 6-6" /></svg>;
}
export function ChevronRightIcon() {
  return <svg {...base}><path d="m9 6 6 6-6 6" /></svg>;
}
export function CloseIcon() {
  return <svg {...base}><path d="m6 6 12 12M6 18 18 6" /></svg>;
}
export function ActivityIcon() {
  return <svg {...base}><rect x="4" y="3" width="16" height="18" rx="2" /><path d="M8 8h8M8 12h8M8 16h4" /></svg>;
}
export function MonitorIcon() {
  return <svg {...base}><rect x="3" y="4" width="18" height="13" rx="2" /><path d="M8 21h8M12 17v4m-6-10 3-2 3 4 3-3 3 1" /></svg>;
}
export function LogoutIcon() {
  return <svg {...base}><path d="M10 4H5a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1h5m5-12 4 4-4 4M9 12h11" /></svg>;
}
