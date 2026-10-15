// 실행기 연결 도우미(v13+)가 알린 진행 단계 → 웹 '매크로 만들기 가이드'가 따라 그릴 화면.
// 서버는 정해진 값만 받아 두지만, 웹도 모르는 값은 버린다 — 키 · 주소 · 문장은 애초에 오지 않는다.

const STAGES = new Set(["ready", "ask", "ip", "perm", "keys", "existing", "manual", "checking", "result_ok", "result_fail", "started", "closed"]);
const PATHS = new Set(["", "new", "existing"]);
const CHECK_STATES = new Set(["ok", "bad", "wait", "na"]);
const PROBLEMS = new Set(["", "ip", "auth", "order", "order_size", "network", "other"]);
const EXCHANGES = new Set(["binance", "upbit", "bithumb"]);
// 바이낸스 도우미(실행기 v19+)가 안내하는 사이트 — testnet(현물 테스트넷) · demo(선물 데모) · live(실거래).
const BINANCE_ENVS = new Set(["testnet", "demo", "live"]);

export const NEW_KEY_STEPS = ["허용 IP 등록", "권한·발급", "키 복사", "매크로 시작"];
export const EXISTING_KEY_STEPS = ["기존 키 넣기", "매크로 시작"];
// '처음이에요' 의 단계 — 실행기 connection_wizard.FLOWS 와 같은 순서. 국내는 허용 IP 를 먼저 등록하고 발급하고,
// 바이낸스는 키를 만든 뒤 그 키에 IP · 권한을 건다(테스트넷 · 데모에는 IP 제한이 없어 두 단계).
const FLOWS = {
  domestic: { stages: ["ip", "perm", "keys"], labels: NEW_KEY_STEPS },
  practice: { stages: ["perm", "keys"], labels: ["키 만들기", "키 복사", "매크로 시작"] },
  live: { stages: ["perm", "keys", "ip"], labels: ["키 만들기", "키 복사", "허용 IP · 주문 권한", "매크로 시작"] },
};

export function helperFlow(progress) {
  if (progress?.exchange !== "binance") return FLOWS.domestic;
  return progress.env === "live" ? FLOWS.live : FLOWS.practice;
}

// 단계 제목 — 실행기 도우미의 제목(connection_wizard.TITLES)과 같은 말.
export function stageTitle(progress, stage) {
  if (progress?.exchange === "binance") {
    return { perm: "키 만들기", keys: "키 복사", ip: "허용 IP · 주문 권한", existing: "기존 키 넣기", manual: "직접 입력" }[stage] || "";
  }
  return { ip: "허용 IP 등록", perm: "권한 선택 · 키 발급", keys: "키 복사", existing: "기존 키 넣기", manual: "직접 입력" }[stage] || "";
}

// 결과 화면의 제목 — 실행기 도우미와 같은 말(runner/connection.py check_view).
export const PROBLEM_HEADLINES = {
  ip: "허용 IP 확인 필요",
  auth: "키 확인 필요",
  order: "주문 권한 확인 필요",
  network: "연결 확인 필요",
  other: "연결 확인 필요",
};

// 바이낸스는 실행기와 같은 제목을 쓴다 — IP 제한이 없는 키는 '등록 필요', -2015 는 '확인 필요'.
export function problemHeadline(progress) {
  if (progress?.exchange === "binance" && progress.problem === "ip" && progress.checks?.auth === "ok") return "허용 IP 등록 필요";
  return PROBLEM_HEADLINES[progress?.problem] || "연결 확인 필요";
}

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
    env: exchange === "binance" && BINANCE_ENVS.has(raw.env) ? raw.env : "",
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
  const flow = helperFlow(progress);
  const labels = existing ? EXISTING_KEY_STEPS : flow.labels;
  const last = labels.length - 1;
  let current = -1;
  let doneUntil = -1;
  const stage = progress?.stage || "";
  const finished = stage === "result_ok" || (stage === "ready" && progress?.connected) || stage === "started";
  if (existing) {
    if (["existing", "manual", "checking", "result_fail"].includes(stage)) current = 0;
    if (finished) {
      doneUntil = 0;
      current = 1;
    }
  } else if (flow.stages.includes(stage)) {
    current = flow.stages.indexOf(stage);
    doneUntil = current - 1;
  } else if (["manual", "checking", "result_fail"].includes(stage)) {
    // 직접 입력 · 확인 중 · 확인 필요는 마지막 발급 단계에 선다.
    current = flow.stages.length - 1;
    doneUntil = current - 1;
  } else if (finished) {
    doneUntil = flow.stages.length - 1;
    current = last;
  }
  if (stage === "started") doneUntil = last;
  return labels.map((label, index) => ({
    label,
    state: index <= doneUntil ? "done" : index === current ? "current" : "todo",
  }));
}

// 결과 세 줄의 오른쪽 글자 — 실행기와 같은 말(바이낸스는 runner/connection.py _BINANCE_VIEWS 의 notes).
// 바이낸스 실거래의 '허용 IP' 빨강은 둘 중 하나다: 키는 통과했는데 IP 제한이 없다 / -2015(IP 또는 키) — 키 줄로 가른다.
export function checkRowNote(row, state, progress = null) {
  if (progress?.exchange === "binance") {
    const live = progress.env === "live";
    if (row === "ip" && state === "na") return live ? "IP 제한 없음" : "필요 없음";
    if (row === "ip" && state === "bad") return progress.checks?.auth === "ok" ? "IP 제한 없음" : "IP 또는 키 불일치";
    if (row === "order" && state === "bad") return "주문 권한 꺼짐";
  }
  if (state === "bad") return { ip: "등록된 IP와 다름", auth: "키가 맞지 않음", order: "주문하기 권한 없음" }[row] || "";
  if (state === "na") return "확인 불가";
  if (state === "wait") return "대기";
  return "";
}
