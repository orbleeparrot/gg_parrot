function executionState(session) {
  if (session.status !== "running" || session.position_uncertain) {
    return JSON.stringify([session.status, !!session.position_uncertain]);
  }
  if (session.stopping) return JSON.stringify([session.status, "stopping", session.stop_mode || ""]);
  return JSON.stringify([session.status, !!session.connected, !!session.in_position,
    session.in_position ? session.position_qty ?? null : null,
    session.in_position ? session.position_side || "long" : null]);
}

export const executionModule = {
  key: "execution",
  label: "실행 상태",
  entitlement: "agent.execution",
  minimumPlan: "free",
  availability: "live",
  buildEvents({ session, previousSession, receivedAt }) {
    if (!session) return [];
    const previous = previousSession?.session_id === session.session_id ? previousSession : null;
    if (previous && executionState(previous) === executionState(session)) return [];
    const running = session.status === "running";
    const failed = session.status === "error" || session.position_uncertain;
    let title;
    let summary;
    if (session.position_uncertain) {
      title = "포지션 확인 필요";
      summary = "주문 체결을 확정하지 못했어요. 거래소에서 주문과 잔여 포지션을 확인하세요.";
    } else if (!running) {
      title = session.status === "error" ? "실행 오류" : "실행 종료";
      summary = session.note || "종료된 실행";
    } else if (session.stopping) {
      title = session.stop_mode === "close_and_stop" ? "청산 후 종료 요청" : "매크로 종료 요청";
      summary = "실행기의 처리 결과를 확인하고 있어요. 완료 보고가 도착하면 갱신됩니다.";
    } else if (previous && !!previous.in_position !== !!session.in_position) {
      title = session.in_position ? "포지션 진입" : "포지션 청산";
      summary = session.in_position
        ? `${session.position_side === "short" ? "숏" : "롱"} 포지션 진입이 실행기에서 확인됐어요.`
        : "실행기에서 포지션 청산을 확인했어요.";
    } else if (!session.connected) {
      if (previous && !previous.connected) return [];
      title = "실행기 응답 끊김";
      summary = "실행기와의 연결을 확인하세요. 새로운 상태 보고를 기다리고 있어요.";
    } else if (previous && !previous.connected) {
      title = "실행기 연결 복구";
      summary = "실행기의 상태 보고가 다시 도착했어요.";
    } else if (previous?.in_position && session.in_position
      && (previous.position_qty !== session.position_qty || previous.position_side !== session.position_side)) {
      title = "포지션 변경";
      summary = `${session.position_side === "short" ? "숏" : "롱"} · 현재 수량 ${session.position_qty ?? "확인 중"}`;
    } else {
      // The dock already displays ordinary connection/position status.
      return [];
    }
    return [{
      id: `execution-${session.session_id}-${executionState(session)}`,
      repeatable: true,
      module: "execution",
      severity: failed ? "critical" : running && !session.connected ? "warning" : "info",
      expression: failed ? "critical" : running && !session.connected ? "warning" : "calm",
      title, summary,
      occurredAt: receivedAt || Date.now(),
      sourceLabel: "매크로 실행 상태",
    }];
  },
};
