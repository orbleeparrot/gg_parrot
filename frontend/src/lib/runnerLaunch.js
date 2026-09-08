// 빠른 실행에서 고른 매크로가 실행기에서 실제로 시작됐는지 판별한다.
// 실행기는 세션을 만들 때 user_macro_id 를 함께 보고하므로, 마지막 단계에 들어올 때
// 잡아 둔 '이미 실행 중' 목록(기준선)에 없던 새 세션 중 같은 매크로를 찾는다.
// 옛 실행기는 user_macro_id 를 안 보내므로 종목이 같은 새 세션으로 대신 본다.
export function findLaunchedSession(activeSessions, baselineIds, selected) {
  if (!selected || !Array.isArray(activeSessions)) return null;
  const baseline = baselineIds instanceof Set ? baselineIds : new Set(baselineIds || []);
  const fresh = activeSessions.filter((session) => (
    session
    && session.status === "running"
    && !baseline.has(session.session_id)
  ));
  const byMacro = fresh.find((session) => (
    session.user_macro_id != null && selected.id != null && session.user_macro_id === selected.id
  ));
  if (byMacro) return byMacro;
  return fresh.find((session) => (
    session.user_macro_id == null && !!selected.symbol && session.symbol === selected.symbol
  )) || null;
}


// 런치 티켓 상태 응답 → 마법사 단계. 거절(rejected)은 기다릴 일이 아니라 안내할 일이다.
export function launchPhaseFromTicketStatus(data) {
  const status = String(data?.status || "").toLowerCase();
  if (status === "claimed" || data?.claimed === true || !!data?.claimed_at) {
    return { phase: "claimed" };
  }
  if (status === "rejected") {
    return {
      phase: "outdated",
      runnerVersion: String(data?.runner_version || ""),
      minVersion: String(data?.min_runner_version || ""),
    };
  }
  if (["expired", "cancelled", "revoked"].includes(status)) return { phase: "expired" };
  return { phase: null };
}
