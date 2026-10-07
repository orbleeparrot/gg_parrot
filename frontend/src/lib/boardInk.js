// 게시판 글자색 — 편집기 팔레트 6색. 저장은 고른 색 값(hex) 그대로 두고, 화면에서는 테마마다
// 읽히는 색으로 바꿔 그린다(Board.css --board-ink-*). 한 가지 색으로는 흰 바탕과 검은 바탕 모두에서
// 4.5:1 을 넘길 수 없기 때문이다. 팔레트 밖의 색(다른 테마·사이트에서 붙여 넣은 색)은 버린다 —
// 다크 화면의 글자색(#dde1e7)이 박힌 글이 라이트에서 안 보이던 원인(GG-001).
export const BOARD_INKS = [
  { key: "red", label: "빨강", hex: "#f6465d" },
  { key: "green", label: "초록", hex: "#0ecb81" },
  { key: "blue", label: "파랑", hex: "#3b82f6" },
  { key: "orange", label: "주황", hex: "#f59e0b" },
  { key: "gold", label: "금색", hex: "#a68000" },
  { key: "gray", label: "회색", hex: "#8b95a5" },
];

const rgbOf = (hex) => {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
};
const BY_RGB = new Map(BOARD_INKS.map((ink) => [rgbOf(ink.hex).join(","), ink.key]));

// "#f6465d" · "rgb(246, 70, 93)" · "rgb(246 70 93)" → "red". 팔레트 밖이거나 반투명이면 null.
export function inkKeyOf(color) {
  const value = String(color || "").trim().toLowerCase();
  if (/^#[0-9a-f]{6}$/.test(value)) return BY_RGB.get(rgbOf(value).join(",")) || null;
  const m = value.match(/^rgba?\(\s*(\d{1,3})[\s,]+(\d{1,3})[\s,]+(\d{1,3})(?:\s*[,/]\s*([\d.]+)(%?))?\s*\)$/);
  if (!m) return null;
  if (m[4] !== undefined && (m[5] ? Number(m[4]) < 100 : Number(m[4]) < 1)) return null;
  return BY_RGB.get([m[1], m[2], m[3]].join(",")) || null;
}

const COLOR_PROPERTIES = ["color", "background", "background-color", "-webkit-text-fill-color"];

// 글 보기 — 팔레트 색은 data-ink 로 바꾸고(색은 CSS 가 테마별로), 나머지 색·배경색은 뗀다.
// 정렬·크기·강조·링크·사진 크기는 그대로 둔다. 글 자체(서버 HTML)는 고치지 않는다.
export function applyBoardInks(root) {
  for (const element of root.querySelectorAll("[style]")) {
    const key = inkKeyOf(element.style.color);
    for (const property of COLOR_PROPERTIES) element.style.removeProperty(property);
    if (key) element.setAttribute("data-ink", key);
    if (!element.getAttribute("style")) element.removeAttribute("style");
  }
  return root;
}

// 편집기에 붙여 넣는 HTML — 팔레트 밖 글자색과 모든 배경색을 뗀다(편집기 안에서부터 보이는 대로 저장되게).
export function stripForeignColors(html) {
  if (typeof document === "undefined") return html;
  const template = document.createElement("template");
  template.innerHTML = html;
  for (const element of template.content.querySelectorAll("[style]")) {
    const keep = inkKeyOf(element.style.color) ? element.style.color : "";
    for (const property of COLOR_PROPERTIES) element.style.removeProperty(property);
    if (keep) element.style.setProperty("color", keep);
    if (!element.getAttribute("style")) element.removeAttribute("style");
  }
  return template.innerHTML;
}
