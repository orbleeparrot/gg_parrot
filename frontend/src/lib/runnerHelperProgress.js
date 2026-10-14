// 실행기 연결 도우미(v13+)가 알린 진행 단계 → 웹 '매크로 만들기 가이드'가 따라 그릴 화면.
// 서버는 정해진 값만 받아 두지만, 웹도 모르는 값은 버린다 — 키 · 주소 · 문장은 애초에 오지 않는다.

const STAGES = new Set(["ready", "ask", "ip", "perm", "keys", "existing", "manual", "checking", "result_ok", "result_fail", "started", "closed"]);
const PATHS = new Set(["", "new", "existing"]);
const CHECK_STATES = new Set(["ok", "bad", "wait", "na"]);
const PROBLEMS = new Set(["", "ip", "auth", "order", "order_size", "network", "other"]);
const EXCHANGES = new Set(["binance", "upbit", "bithumb"]);

export const NEW_KEY_STEPS = ["허용 IP 등록", "권한·발급", "키 복사", "매크로 시작"];
export const EXISTING_KEY_STEPS = ["기존 키 넣기", "매크로 시작"];

// 결과 화면의 제목 — 실행기 도우미와 같은 말(runner/connection.py check_view).
export const PROBLEM_HEADLINES = {
  ip: "허용 IP 확인 필요",
  auth: "키 확인 필요",
  order: "주문 권한 확인 필요",
  network: "연결 확인 필요",
  other: "연결 확인 필요",
};

export function parseHelperProgress(raw) {
  if (!raw || typeof raw !== "object") return null;
  const stage = String(raw.stage || "");
  const exchange = String(raw.exchange || "");
  if (!STAGES.has(stage) || !EXCHANGES.has(exchange)) return null;
  const path = PATHS.has(raw.path) ? raw.path : "";
  const checks = {};
  for (const row of ["auth", "ip", "order"]) {
    const value = raw.checks?.[row];
    checks[row] = CHECK_STATES.has(value) ? value : "wait";
  }
  return {
    stage,
    exchange,
    path,
    slots: { access: raw.slots?.access === true, secret: raw.slots?.secret === true },
    checks,
    problem: PROBLEMS.has(raw.problem) ? raw.problem : "",
    connected: raw.connected === true,
  };
}

// 진행 표시 칸 — 처음 발급은 넷(허용 IP · 권한·발급 · 키 복사 · 매크로 시작), 기존 키는 둘(기존 키 넣기 · 매크로 시작).
// 각 칸은 done(끝남) · current(지금) · todo(아직).
export function helperTrack(progress) {
  const existing = progress?.path === "existing" || progress?.stage === "existing";
  const labels = existing ? EXISTING_KEY_STEPS : NEW_KEY_STEPS;
  const last = labels.length - 1;
  let current = -1;
  let doneUntil = -1;
  const stage = progress?.stage || "";
  if (existing) {
    if (["existing", "manual", "checking", "result_fail"].includes(stage)) current = 0;
    if (stage === "result_ok" || (stage === "ready" && progress?.connected) || stage === "started") {
      doneUntil = 0;
      current = 1;
    }
  } else if (stage === "ip") current = 0;
  else if (stage === "perm") {
    doneUntil = 0;
    current = 1;
  } else if (["keys", "manual", "checking", "result_fail"].includes(stage)) {
    doneUntil = 1;
    current = 2;
  } else if (stage === "result_ok" || (stage === "ready" && progress?.connected) || stage === "started") {
    doneUntil = 2;
    current = 3;
  }
  if (stage === "started") doneUntil = last;
  return labels.map((label, index) => ({
    label,
    state: index <= doneUntil ? "done" : index === current ? "current" : "todo",
  }));
}

// 결과 세 줄의 오른쪽 글자 — 실행기와 같은 말.
export function checkRowNote(row, state) {
  if (state === "bad") return { ip: "등록된 IP와 다름", auth: "키가 맞지 않음", order: "주문하기 권한 없음" }[row] || "";
  if (state === "na") return "확인 불가";
  if (state === "wait") return "대기";
  return "";
}
