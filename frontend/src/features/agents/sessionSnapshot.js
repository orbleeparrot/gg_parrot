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
