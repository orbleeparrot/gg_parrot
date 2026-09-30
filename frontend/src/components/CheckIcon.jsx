// 체크 표시 — Lucide `check` (ISC License, Copyright (c) Lucide Contributors). Icon Wiki(iconwiki.aoo.kr)에서 골랐다.
// 글자 '✓' 는 서체마다 모양·굵기·기준선이 달라 어색해서, 앱의 다른 선 아이콘(둥근 끝·둥근 모서리)과 같은 SVG 를 쓴다.
export default function CheckIcon({ size = 18, strokeWidth = 2.25, className = "" }) {
  return (
    <svg className={className} width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}
