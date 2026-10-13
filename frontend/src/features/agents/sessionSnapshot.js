// A missing session and a missing chart must not compare as a matching symbol.
export function activeSessionChart(snapshot, session, exchange = "binance") {
  if (!snapshot || !session?.symbol || snapshot.symbol !== session.symbol) return null;
  return (snapshot.exchange || "binance") === exchange ? snapshot : null;
}

export function requireSessionSnapshot(data) {
  const validRows = (rows) => Array.isArray(rows)
    && rows.every((row) => row && typeof row === "object" && !Array.isArray(row));
  if (!data || !validRows(data.active) || !validRows(data.recent)) {
    throw new Error("실행 목록 응답을 확인할 수 없어요. 다시 불러와 주세요.");
  }
  return data;
}


// 결과 화면(끝난 세션)을 보는 중 실행기에서 새로 시작한 세션 — 화면이 그리로 넘어간다.
// 처음 본 실행 중 세션만(이미 보던 것은 아니다). 여러 개면 보던 세션과 같은 매크로를 먼저 고른다.
export function newlyStartedSession(knownIds, active, shown) {
  if (!(knownIds instanceof Set) || !Array.isArray(active)) return null;
  const fresh = active.filter((session) => session && session.status === "running" && !knownIds.has(session.session_id));
  if (!fresh.length) return null;
  const sameMacro = shown?.user_macro_id != null
    ? fresh.find((session) => session.user_macro_id === shown.user_macro_id) : null;
  return sameMacro || fresh[0];
}
