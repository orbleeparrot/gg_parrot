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


// 런치 티켓을 발급한 서버 시각(ms). 응답에는 만료 시각만 있어 수명(120초)을 뺀다. 모르면 0(쓰지 않음).
export const LAUNCH_TICKET_TTL_MS = 120_000;
export function ticketIssuedAtMs(ticket) {
  const expires = Date.parse(String(ticket?.expires_at || ""));
  return Number.isFinite(expires) ? expires - LAUNCH_TICKET_TTL_MS : 0;
}

// 실행기를 연 뒤(서버 시각 sinceMs 이후) 이 매크로로 시작된 실행 중 세션. 서버가 붙인 시각끼리만 비교한다.
export function findSessionStartedSince(activeSessions, selected, sinceMs) {
  if (!sinceMs || !selected || selected.id == null || !Array.isArray(activeSessions)) return null;
  return activeSessions.find((session) => (
    session
    && session.status === "running"
    && session.user_macro_id === selected.id
    && Date.parse(String(session.started_at || "")) >= sinceMs
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
