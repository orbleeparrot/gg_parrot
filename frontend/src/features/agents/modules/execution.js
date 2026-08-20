export const executionModule = {
  key: "execution",
  label: "실행 상태",
  entitlement: "agent.execution",
  minimumPlan: "free",
  availability: "live",
  buildEvents({ session, observedAt }) {
    if (!session) return [];
    const running = session.status === "running";
    const failed = session.status === "error";
    return [{
      id: `execution-${session.session_id}-${session.status}-${session.connected}`,
      module: "execution",
      severity: failed ? "critical" : running && !session.connected ? "warning" : "info",
      expression: failed ? "critical" : running && !session.connected ? "warning" : "calm",
      title: running
        ? (session.connected ? "실행기 연결됨" : "실행기 응답 대기")
        : (session.status === "error" ? "실행 오류" : "실행 종료"),
      summary: running
        ? `${session.testnet ? "테스트넷" : "실거래"} · ${session.in_position ? "포지션 보유 중" : "현재 포지션 없음"}`
        : (session.note || "종료된 실행"),
      occurredAt: observedAt || Date.now(),
      sourceLabel: "매크로 실행 상태",
    }];
  },
};
