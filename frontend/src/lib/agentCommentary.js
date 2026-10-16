// 껄무새 중계 말풍선 — 서버(agent_commentary.py)가 만든 것을 화면 모양으로 바꾸는 순수 함수들.
// 말할 순간 · 글자는 서버가 정한다. 여기서는 합치기 · 날짜 줄 · 시각 · 링크만.
import { activityLogLink } from "../features/agents/activityEvidence.js";

export const MAX_ITEMS = 400;
const FACES = new Set(["calm", "curious", "focused", "signal", "warning", "critical"]);
const KST_MS = 9 * 60 * 60 * 1000;

// 새로 받은 말풍선을 id 순으로 합친다(같은 id 는 새 값으로). 너무 길어지면 오래된 것부터 버린다.
export function mergeSayings(current, incoming) {
  const byId = new Map((current || []).map((item) => [item.id, item]));
  for (const item of incoming || []) {
    if (item && Number.isSafeInteger(item.id) && item.id > 0) byId.set(item.id, item);
  }
  return [...byId.values()].sort((a, b) => a.id - b.id).slice(-MAX_ITEMS);
}

function kstParts(ms) {
  const date = new Date(Number(ms) + KST_MS);
  return { y: date.getUTCFullYear(), m: date.getUTCMonth() + 1, d: date.getUTCDate(), h: date.getUTCHours(), min: date.getUTCMinutes() };
}

export function kstClock(ms) {
  if (!Number.isFinite(Number(ms)) || Number(ms) <= 0) return "";
  const { h, min } = kstParts(ms);
  return `${String(h).padStart(2, "0")}:${String(min).padStart(2, "0")}`;
}

export function kstDay(ms) {
  if (!Number.isFinite(Number(ms)) || Number(ms) <= 0) return "";
  const { m, d } = kstParts(ms);
  return `${m}월 ${d}일`;
}

// 날짜가 바뀌는 자리에 날짜 줄을 끼운다: [{ type: "day", label }, { type: "say", item }, ...]
export function withDayRows(items) {
  const rows = [];
  let last = "";
  for (const item of items || []) {
    const day = kstDay(item.at_ms);
    if (day && day !== last) {
      rows.push({ type: "day", key: `day-${day}-${item.id}`, label: day });
      last = day;
    }
    rows.push({ type: "say", key: `say-${item.id}`, item });
  }
  return rows;
}

export function faceSrc(face) {
  return `/brand/agent/ggparrot-agent-${FACES.has(face) ? face : "calm"}-v1.svg`;
}

// 칩이 여는 곳: 체결 · 기록은 활동 로그의 그 줄, 글 · 기사는 원문(https 만). 없으면 글자만.
export function chipTarget(chip, sessionId) {
  if (!chip || typeof chip !== "object") return null;
  if (typeof chip.href === "string" && /^https:\/\//.test(chip.href)) return { external: chip.href };
  const log = chip.event_id ? activityLogLink(sessionId, chip.event_id) : "";
  return log ? { internal: log } : null;
}

// 손실 말풍선의 [청산하고 종료] · [계속 두기] — 실행 중이고 아직 포지션이 있을 때, 가장 최근 손실 말풍선에만.
export function actionableId(items, session) {
  if (!session || session.status !== "running" || session.stopping || !session.in_position) return null;
  for (let index = (items || []).length - 1; index >= 0; index -= 1) {
    const item = items[index];
    if (item.kind === "fill") return null; // 그 뒤로 새로 사고판 일이 있으면 지난 손실 말풍선은 끝난 이야기
    if (Array.isArray(item.actions) && item.actions.includes("close_and_stop")) return item.id;
  }
  return null;
}

export function subText(sub) {
  if (typeof sub === "string") return { b: "", t: sub };
  if (!sub || typeof sub !== "object") return { b: "", t: "" };
  return { b: typeof sub.b === "string" ? sub.b : "", t: typeof sub.t === "string" ? sub.t : "" };
}
