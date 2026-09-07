export const executionModule = {
  key: "execution",
  label: "실행 상태",
  entitlement: "agent.execution",
  minimumPlan: "free",
  availability: "live",
  buildEvents({ session, observedAt }) {
    if (!session) return [];
    const running = session.status === "running";
    const failed = session.status === "error" || session.position_uncertain;
    return [{
      id: `execution-${session.session_id}-${session.status}-${session.connected}-${session.stop_mode}`,
      module: "execution",
      severity: failed ? "critical" : running && !session.connected ? "warning" : "info",
      expression: failed ? "critical" : running && !session.connected ? "warning" : "calm",
      title: session.position_uncertain ? "포지션 확인 필요" : running
        ? (session.stopping ? (session.stop_mode === "close_and_stop" ? "청산 후 종료 요청" : "매크로 종료 요청") : session.connected ? "실행기 연결됨" : "실행기 응답 대기")
        : (session.status === "error" ? "실행 오류" : "실행 종료"),
      summary: session.position_uncertain
        ? "주문 체결을 확정하지 못했어요. 거래소에서 주문과 잔여 포지션을 확인하세요."
        : running
        ? (session.stopping ? "실행기의 처리 결과를 확인하고 있어요. 완료 보고가 도착하면 갱신됩니다." : `${session.testnet ? "테스트넷" : "실거래"} · ${session.in_position ? "포지션 보유 중" : "현재 포지션 없음"}${session.note ? ` · ${session.note}` : ""}`)
        : (session.note || "종료된 실행"),
      occurredAt: observedAt || Date.now(),
      sourceLabel: "매크로 실행 상태",
    }];
  },
};
