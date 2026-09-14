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
};

function severityFor(kind) {
  if (kind === "error") return "critical";
  if (kind === "order" || kind === "fill") return "signal";
  if (kind === "stop") return "warning";
  return "info";
}

function expressionFor(kind) {
  if (kind === "error") return "critical";
  if (kind === "order" || kind === "fill") return "signal";
  if (kind === "stop") return "warning";
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
      return {
        id: `runner-log-${session.session_id}-${row.id}`,
        module: "runner_log",
        severity: severityFor(kind),
        expression: expressionFor(kind),
        title: row.message,
        summary: "",
        occurredAt: row.ts,
        sourceLabel: `실행기 로그 · ${KIND_LABEL[kind]}`,
        // 화면을 열 때 한꺼번에 들어오는 옛 줄까지 알림으로 읽지 않게.
        notify: false,
      };
    });
  },
};
