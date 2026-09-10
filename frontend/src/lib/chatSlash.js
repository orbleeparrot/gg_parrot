// 채팅의 매크로 고르기 — 입력칸에 `/` 를 치면 매크로 목록이 뜨고, `/제목` 처럼 이어 치면 걸러진다.
// 고르면 `[macro:id]` 토큰이 본문에 들어가고 채팅은 그 자리에 매크로 카드를 그린다.

const SLASH = /^\/([^\n]*)$/;

/** 입력칸 전체가 `/…` 이면 검색어(빈 문자열 포함), 아니면 null. */
export function slashQuery(text) {
  const match = SLASH.exec(String(text || ""));
  return match ? match[1] : null;
}

/** 검색어로 매크로 후보 거르기 — 종목·글쓴이·전략을 모두 본다. */
export function filterMacros(entries, query, limit = 6) {
  const needle = String(query || "").trim().toLowerCase();
  const rows = (entries || []).filter((entry) => entry && entry.id != null);
  if (!needle) return rows.slice(0, limit);
  const hit = (value) => String(value || "").toLowerCase().includes(needle);
  return rows.filter((entry) => hit(entry.symbol) || hit(entry.username) || hit(entry.nickname) || hit(entry.human_summary)).slice(0, limit);
}

/** 고른 매크로를 본문에 넣는다 — `/검색어` 는 지우고 토큰을 남긴다. */
export function applyMacroPick(text, entryId) {
  const query = slashQuery(text);
  const rest = query === null ? String(text || "") : "";
  return `${rest}${rest && !rest.endsWith(" ") ? " " : ""}[macro:${entryId}] `;
}
