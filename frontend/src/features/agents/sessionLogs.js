export const MAX_SESSION_LOG_ITEMS = 500;

export const SESSION_LOG_CATEGORIES = Object.freeze([
  { key: "all", label: "전체" },
  { key: "trade", label: "매매" },
  { key: "execution", label: "실행" },
  { key: "news", label: "뉴스" },
  { key: "error", label: "오류" },
]);

const CATEGORIES = new Set(SESSION_LOG_CATEGORIES.slice(1).map(({ key }) => key));
const NEWS_ERROR_STATES = new Set(["failed", "error", "rate_limited"]);
const NEWS_WAIT_STATES = new Set(["pending", "collecting", "empty", "idle", "unavailable", "unknown", "partial", "processing", "stopped"]);
const KIND_LABELS = {
  start: "실행 시작", info: "실행 기록", signal: "조건 판정", order: "주문",
  fill: "체결", error: "오류", stop: "종료", warn: "주의",
  news_delivered: "뉴스 제공", news_received: "뉴스 제공",
};

function serverId(value) {
  if (typeof value !== "number" && (typeof value !== "string" || !/^\d+$/.test(value))) return null;
  const id = Number(value);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

function parseTimestamp(value) {
  let parsed;
  if (typeof value === "number") parsed = value;
  else if (typeof value === "string" && value.trim()) {
    // Database UTC timestamps can be serialized without their zone. Never let
    // the browser's local timezone change the recorded occurrence time.
    const text = value.trim();
    if (!/^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:\d{2})?)?$/.test(text)) return null;
    parsed = Date.parse(/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(text)
      ? `${text}Z` : text);
  }
  return Number.isFinite(parsed) && parsed > 0 && parsed <= 8.64e15 ? parsed : null;
}

export function validateSessionLogResponse(response, sessionId) {
  const expected = serverId(sessionId);
  if (!expected || !response || serverId(response.session_id) !== expected || !Array.isArray(response.items)) {
    throw new Error("해당 매크로의 활동 로그 응답을 확인하지 못했어요.");
  }
  return response;
}

export function mergeSessionLogItems(current, incoming, { maxItems = MAX_SESSION_LOG_ITEMS } = {}) {
  const byId = new Map();
  for (const items of [current, incoming]) {
    for (const item of Array.isArray(items) ? items : []) {
      const id = item && typeof item === "object" ? serverId(item.id) : null;
      if (id !== null) byId.set(id, { ...byId.get(id), ...item });
    }
  }
  const limit = Number.isSafeInteger(maxItems) && maxItems > 0
    ? Math.min(MAX_SESSION_LOG_ITEMS, maxItems) : MAX_SESSION_LOG_ITEMS;
  // The API paginates by immutable reception id. Delayed runner timestamps
  // must not reorder loaded pages or make an older event look newly received.
  return [...byId.values()].sort((left, right) => serverId(right.id) - serverId(left.id)).slice(0, limit);
}

export function sessionLogCategory(item) {
  if (CATEGORIES.has(item?.category)) return item.category;
  if (item?.kind === "error") return "error";
  if (["news", "news_receipt"].includes(item?.source)
    || ["news", "news_error", "news_delivered", "news_received"].includes(item?.kind)) return "news";
  if (item?.kind === "order" || item?.kind === "fill") return "trade";
  return "execution";
}

export function isSessionLogError(item) {
  if (sessionLogCategory(item) === "error" || item?.kind === "error" || item?.kind === "news_error") return true;
  return sessionLogCategory(item) === "news" && [item?.details?.status, item?.details?.collection_status,
    item?.details?.translation_status].some((status) => NEWS_ERROR_STATES.has(status));
}

export function matchesSessionLogCategory(item, category) {
  if (category === "all") return true;
  if (category === "error") return isSessionLogError(item);
  return sessionLogCategory(item) === category;
}

export function sessionLogTimestamp(item) {
  return parseTimestamp(item?.ts) ?? parseTimestamp(item?.received_at);
}

export function formatSessionLogTime(value) {
  const parsed = parseTimestamp(value);
  if (parsed === null) return "시각 확인 불가";
  const date = new Date(parsed + 9 * 60 * 60 * 1000);
  if (!Number.isFinite(date.getTime())) return "시각 확인 불가";
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getUTCFullYear()}.${pad(date.getUTCMonth() + 1)}.${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())} KST`;
}

export function safeSessionLogUrl(value) {
  if (typeof value !== "string" || /[\u0000-\u001f\u007f]/.test(value)) return "";
  try {
    const url = new URL(value.trim());
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
  } catch {
    return "";
  }
}

export function sessionLogKindLabel(item) {
  if (sessionLogCategory(item) === "news") {
    if (NEWS_ERROR_STATES.has(item?.details?.translation_status)) return "뉴스 번역·요약 오류";
    if (isSessionLogError(item)) return "뉴스 수집 오류";
    const status = item?.details?.collection_status || item?.details?.status;
    if (NEWS_WAIT_STATES.has(status)) return "뉴스 수집 상태";
    const count = item?.details?.article_count ?? item?.details?.count;
    if (Number.isSafeInteger(count) && count > 0) return "뉴스 제공";
    if (["news_delivered", "news_received"].includes(item?.kind) && !status) return "뉴스 제공";
    return "뉴스 수집 상태";
  }
  return KIND_LABELS[item?.kind] || "실행 기록";
}

export function sessionLogSourceLabel(item) {
  if (typeof item?.source_label === "string" && item.source_label.trim()) return item.source_label.trim().slice(0, 120);
  if (item?.source === "runner") return "Windows 실행기";
  if (item?.source === "execution") return "실행 기록";
  if (item?.source === "news") return "뉴스 서비스";
  if (item?.source === "news_receipt") return "뉴스 응답 수신 확인";
  return "서버 기록";
}
