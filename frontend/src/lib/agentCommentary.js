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

export function subText(sub) {
  if (typeof sub === "string") return { b: "", t: sub };
  if (!sub || typeof sub !== "object") return { b: "", t: "" };
  return { b: typeof sub.b === "string" ? sub.b : "", t: typeof sub.t === "string" ? sub.t : "" };
}

// 채팅 필터(시안 A, 10/17) — 말풍선 종류(서버 agent_commentary 의 kind)를 셋으로 묶는다.
export const CHAT_GROUPS = Object.freeze([
  { key: "trade", label: "거래 관련", kinds: ["start", "fill", "wait", "problem", "end"] },
  { key: "news", label: "뉴스 소식", kinds: ["post"] },
  { key: "market", label: "손익 · 시세", kinds: ["loss", "gain", "move", "volatility", "quiet"] },
]);
const GROUP_KINDS = new Map(CHAT_GROUPS.map((group) => [group.key, new Set(group.kinds)]));

/** 이 묶음(전체면 "all")에 드는 말풍선인지. 모르는 묶음 이름은 전체로 본다. */
export function inChatGroup(item, group) {
  const kinds = GROUP_KINDS.get(group);
  return !kinds || kinds.has(item?.kind);
}

/** 탭 옆 숫자 — 오늘(한국 시각) 그 묶음 말풍선 수. */
export function todayCount(items, group, now = Date.now()) {
  const today = kstDay(now);
  return (items || []).filter((item) => inChatGroup(item, group) && kstDay(item.at_ms) === today).length;
}

/** 고래 동향(대규모 체결)은 바이낸스 USDT · USDC 거래쌍만 지원한다 — 업비트 · 빗썸은 탭 자체를 보이지 않는다. */
export function supportsWhale(symbol) {
  const value = String(symbol || "").toUpperCase();
  return !value.startsWith("KRW-") && !value.endsWith("_KRW") && /(USDT|USDC)$/.test(value);
}

// 고른 탭은 실행마다 기억한다(이 브라우저만). 저장소를 못 쓰면 그냥 전체.
const VIEW_KEY = "ggp.agent.chat.view.";
export function readChatView(sessionId) {
  try {
    return (sessionId && window.localStorage.getItem(VIEW_KEY + sessionId)) || "all";
  } catch {
    return "all";
  }
}
export function saveChatView(sessionId, view) {
  try {
    if (sessionId) window.localStorage.setItem(VIEW_KEY + sessionId, view);
  } catch {
    // 저장소를 못 쓰면 이 화면에서만 바뀐다.
  }
}
