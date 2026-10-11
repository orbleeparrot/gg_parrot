// 실행기 로그 — 실행기 창에만 남던 줄(신호·주문·체결·오류)을 서버가 heartbeat 로 받아 둔 것.
// 문의가 오면 "언제 어떤 주문이 나갔는지"를 여기서 바로 본다. 이벤트 id 는 서버 행 id 라
// 폴링을 반복해도 같은 줄이 두 번 붙지 않는다.
const KIND_LABEL = {
  start: "실행 시작",
  info: "실행기",
  signal: "조건 판정",
  order: "주문",
  fill: "체결",
  error: "오류",
  stop: "종료",
  warn: "주의",
};
const SERVER_RETRY_MESSAGE = "서버 연결 재시도 중 — 신호 대기(진입 없음, 손절만 로컬에서 봅니다)";

function timestamp(value) {
  const parsed = typeof value === "number" ? value : Date.parse(value || "");
  return Number.isFinite(parsed) && parsed > 0 && parsed <= 8.64e15 ? parsed : null;
}

function severityFor(kind) {
  if (kind === "error") return "critical";
  if (kind === "order" || kind === "fill") return "signal";
  if (kind === "stop" || kind === "warn") return "warning";
  return "info";
}

function expressionFor(kind) {
  if (kind === "error") return "critical";
  if (kind === "order" || kind === "fill") return "signal";
  if (kind === "stop" || kind === "warn") return "warning";
  if (kind === "start") return "focused";
  return "calm";
}

export const runnerLogModule = {
  key: "runner_log",
  label: "실행 로그",
  entitlement: "agent.runner_log",
  minimumPlan: "free",
  availability: "live",
  buildEvents({ session, featureStates }) {
    const state = featureStates?.runner_log;
    if (!session || !state?.data || state.data.session_id !== session.session_id) return [];
    const rows = Array.isArray(state.data.events) ? state.data.events : [];
    // 서버는 최신순으로 주고, 타임라인은 시간순으로 붙인다.
    return [...rows].reverse().map((row) => {
      const kind = KIND_LABEL[row.kind] ? row.kind : "info";
      const heartbeatAt = timestamp(session.last_heartbeat_at);
      const loggedAt = timestamp(row.ts);
      const recovered = row.message === SERVER_RETRY_MESSAGE && heartbeatAt !== null
        && loggedAt !== null && heartbeatAt > loggedAt;
      return {
        id: `runner-log-${session.session_id}-${row.id}`,
        module: "runner_log",
        severity: recovered ? "info" : severityFor(kind),
        expression: recovered ? "calm" : expressionFor(kind),
        title: recovered ? "서버 연결 복구 · 이전 신호 대기 기록" : row.message,
        summary: recovered ? "이 경고 이후 실행기의 상태 보고가 서버에 도착했어요. 과거 연결 지연 기록이며 현재 진입 여부를 뜻하지 않아요." : "",
        ...(recovered ? { originalTitle: row.message, resolvedAt: heartbeatAt } : {}),
        occurredAt: row.ts,
        sourceLabel: `실행기 로그 · ${KIND_LABEL[kind]}`,
        // 화면을 열 때 한꺼번에 들어오는 옛 줄까지 알림으로 읽지 않게.
        notify: false,
      };
    });
  },
};
