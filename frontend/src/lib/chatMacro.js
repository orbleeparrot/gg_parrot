// 채팅의 매크로 언급 — 리더보드 행의 '채팅에 붙여넣기'가 `[macro:12]` 를 클립보드에 넣고,
// 채팅은 그 자리에 매크로 카드를 그린다. 본문은 서버에 글자 그대로 저장된다(스티커와 같은 방식).

export const MACRO_TOKEN_RE = /\[macro:(\d{1,9})\]/g;

export function macroText(entryId) {
  return `[macro:${entryId}]`;
}

/** 본문에 실린 매크로 번호들(나온 순서, 중복 제거). */
export function macroIdsInText(text) {
  const ids = [];
  for (const match of String(text || "").matchAll(MACRO_TOKEN_RE)) {
    const id = Number(match[1]);
    if (!ids.includes(id)) ids.push(id);
  }
  return ids;
}

/**
 * 본문을 글 조각과 매크로 카드 조각으로 나눈다.
 * cards: 서버가 준 [{entry_id, ...}]. 카드가 없는 번호는 글자 그대로 남긴다.
 * 반환: [{type:"text", text} | {type:"macro", card}]
 */
export function splitMacroText(text, cards = []) {
  const byId = new Map((cards || []).map((card) => [Number(card.entry_id), card]));
  const source = String(text || "");
  const parts = [];
  let last = 0;
  for (const match of source.matchAll(MACRO_TOKEN_RE)) {
    const card = byId.get(Number(match[1]));
    if (!card) continue;
    if (match.index > last) parts.push({ type: "text", text: source.slice(last, match.index) });
    parts.push({ type: "macro", card });
    last = match.index + match[0].length;
  }
  if (last < source.length) parts.push({ type: "text", text: source.slice(last) });
  return parts
    .map((part) => (part.type === "text" ? { ...part, text: part.text.replace(/\s+/g, " ").trim() } : part))
    .filter((part) => part.type !== "text" || part.text);
}
