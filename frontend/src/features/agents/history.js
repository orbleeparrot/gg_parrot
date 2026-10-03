// 내 에이전트 종료 기록 표기 — 실행 시간(길이 + 날짜 범위)과 종료 방식.
// 시각은 서버의 ISO(UTC)를 받아 한국 시간으로 쓴다.

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;
const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

function parseIso(value) {
  const ms = Date.parse(String(value || ""));
  return Number.isFinite(ms) ? ms : null;
}

function kstParts(ms) {
  const d = new Date(ms + KST_OFFSET_MS);
  return {
    month: d.getUTCMonth() + 1,
    day: d.getUTCDate(),
    weekday: WEEKDAYS[d.getUTCDay()],
    time: `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`,
    dayKey: `${d.getUTCFullYear()}-${d.getUTCMonth()}-${d.getUTCDate()}`,
  };
}

// 실행 길이 — '5초' · '21분' · '3시간 12분' · '2일 4시간'.
export function runDurationLabel(startedAt, stoppedAt) {
  const start = parseIso(startedAt);
  const end = parseIso(stoppedAt);
  if (start == null || end == null || end < start) return "";
  const total = Math.round((end - start) / 1000);
  if (total < 60) return `${Math.max(1, total)}초`;
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days) return hours ? `${days}일 ${hours}시간` : `${days}일`;
  if (hours) return minutes ? `${hours}시간 ${minutes}분` : `${hours}시간`;
  return `${minutes}분`;
}

// 날짜 범위 — 같은 날이면 '9월 30일 (화) 08:44 – 09:05', 날이 바뀌면 끝에도 날짜를 쓴다.
export function runPeriodLabel(startedAt, stoppedAt) {
  const start = parseIso(startedAt);
  if (start == null) return "";
  const a = kstParts(start);
  const head = `${a.month}월 ${a.day}일 (${a.weekday}) ${a.time}`;
  const end = parseIso(stoppedAt);
  if (end == null) return head;
  const b = kstParts(end);
  return a.dayKey === b.dayKey ? `${head} – ${b.time}` : `${head} – ${b.month}월 ${b.day}일 ${b.time}`;
}

// 종료 방식 한 줄 — 오류는 주의(warn), 나머지는 무채색.
export function endLabel(session) {
  const s = session || {};
  if (s.status === "error") return { text: "실행 오류", tone: "warn" };
  if (s.position_uncertain) return { text: "포지션 확인 필요", tone: "warn" };
  if (s.stop_mode === "close_and_stop") return { text: "청산 후 종료", tone: "muted" };
  if (s.stop_mode === "stop_only") return { text: "매크로만 종료", tone: "muted" };
  if (s.note === "포지션 없이 종료") return { text: "포지션 없이 종료", tone: "muted" };
  if (s.note === "청산 완료 후 종료") return { text: "청산 후 종료", tone: "muted" };
  return { text: "실행기에서 종료", tone: "muted" };
}

export function environmentLabel(session) {
  const s = session || {};
  const net = s.testnet ? "테스트넷" : "실거래";
  const market = s.market === "futures"
    ? `선물 ${Number(s.leverage) || 1}배`
    : "현물";
  return `${net} · ${market}`;
}
