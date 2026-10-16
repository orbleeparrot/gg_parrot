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

export const SESSION_LOG_SEVERITIES = ["DEBUG", "INFO", "WARN", "ERROR", "UNKNOWN"];
export const SESSION_LOG_COMPONENTS = ["runner", "server", "engine", "news_delivery", "browser_observation"];

// One canonical query defines both the request and its cursor/result identity.
// Filters describe server reception time, not a possibly delayed runner clock.
export function normalizeSessionLogQuery(value = {}) {
  const bounded = (input, limit) => typeof input === "string" ? input.trim().slice(0, limit) : "";
  const integer = (input) => Number.isSafeInteger(input) && input > 0 ? input : null;
  return {
    q: bounded(value.q, 200),
    severity: SESSION_LOG_SEVERITIES.includes(value.severity) ? value.severity : "",
    component: /^[a-z][a-z0-9_.-]{0,47}$/.test(value.component || "") ? value.component : "",
    eventName: /^[a-z][a-z0-9_.-]{0,79}$/.test(value.eventName || "") ? value.eventName : "",
    operationId: bounded(value.operationId, 120),
    sinceMs: integer(value.sinceMs), untilMs: integer(value.untilMs),
    contextId: integer(value.contextId),
  };
}

export function sessionLogQueryParams({ category = "all", beforeId, limit = 50, ...filters } = {}) {
  const normalized = normalizeSessionLogQuery(filters);
  const query = new URLSearchParams({ category: category === "all" || CATEGORIES.has(category) ? category : "all",
    limit: String(Number.isSafeInteger(limit) && limit > 0 ? Math.min(limit, 100) : 50) });
  for (const [field, parameter] of [["q", "q"], ["severity", "severity"], ["component", "component"],
    ["eventName", "event_name"], ["operationId", "operation_id"], ["sinceMs", "since_ms"],
    ["untilMs", "until_ms"], ["contextId", "context_id"]]) {
    if (normalized[field]) query.set(parameter, String(normalized[field]));
  }
  if (Number.isSafeInteger(beforeId) && beforeId > 0) query.set("before_id", String(beforeId));
  return query;
}

export function sessionLogSeverity(item) {
  return SESSION_LOG_SEVERITIES.includes(item?.severity) ? item.severity : "UNKNOWN";
}

export function sessionLogComponent(item) {
  return typeof item?.component === "string" && item.component.trim()
    ? item.component.trim().slice(0, 64) : "legacy";
}

const RECORDED_MODE_LABELS = Object.freeze({
  mock: "모의 · 실제 주문 없음",
  testnet: "테스트넷",
  live: "실전 모드 · 체결 검증 아님",
});
const VERIFICATION_NOTES = Object.freeze({
  runner_reported_not_exchange_verified: "실행기 보고 · 거래소 독립 검증 아님",
  server_command_state: "서버 명령 상태 · 거래소 체결 확인 아님",
  server_signal_not_exchange_order: "서버 조건 신호 · 거래소 주문 아님",
  browser_observation_not_exchange_event: "브라우저 관측 · 서버 명령/거래소 체결 증거 아님",
});
const RECONCILIATION_NOTES = Object.freeze({
  simulated_fee_model: "모의 수수료 모델 · 실제 거래소 수수료 아님",
  domestic_trade_quantity_coverage: "국내 거래소 체결 상세의 수량 정합성 · 수수료 금액 확인 아님",
  binance_spot_fill_fee_coverage: "Binance 현물 체결·수수료 상세 정합성 · 수수료 금액 별도 확인 필요",
  binance_futures_quantity_only: "Binance 선물 체결 수량 확인 · 수수료 검증 아님",
  unknown: "어댑터 정합성 검사 범위 미확인",
});

// A row must use its own recorded mode. Never infer it from its message, prices,
// raw exchange status or the currently selected (possibly changed) session mode.
export function sessionLogModeLabel(mode) {
  return typeof mode === "string" && Object.hasOwn(RECORDED_MODE_LABELS, mode) ? RECORDED_MODE_LABELS[mode] : "";
}

function isBrowserNewsReceipt(item) {
  if (item?.event_name === "news.response_received") return true;
  if (item?.event_name && item.event_name !== "news.received") return false;
  return item?.source === "news_receipt" && (!item.source_label || item.source_label === "뉴스 응답 수신 확인");
}

export function sessionLogFieldNote(field, value) {
  if (field === "fees_known") return "실행기 어댑터 정합성 플래그 · 수수료 금액 확인 아님";
  if (field === "reconciliation_scope" && typeof value === "string" && Object.hasOwn(RECONCILIATION_NOTES, value)) return RECONCILIATION_NOTES[value];
  if (field === "verification" && typeof value === "string" && Object.hasOwn(VERIFICATION_NOTES, value)) return VERIFICATION_NOTES[value];
  if (field === "mode") return sessionLogModeLabel(value);
  if (field === "ack_source" && ["runner", "engine"].includes(value)) return value === "runner" ? "실행기 보고" : "서버 엔진";
  if (field === "action" && ["buy", "sell", "short", "cover"].includes(value)) return { buy: "매수", sell: "매도", short: "공매도", cover: "공매도 청산" }[value];
  return "";
}

export function sessionLogObservationScope(item) {
  const details = item?.details || {};
  const notes = [sessionLogModeLabel(details.mode), sessionLogFieldNote("verification", details.verification)].filter(Boolean);
  if (isBrowserNewsReceipt(item)) notes.push("브라우저 응답 수신 · 수집 완료/사용자 열람 증명 아님");
  if (item?.event_name === "news.request_failed") notes.push("서버 요청 실패 기록 · 브라우저 수신 확인 아님");
  if (item?.event_name === "news.response_generated") notes.push("서버 응답 생성 · 브라우저 수신 확인 아님");
  if (item?.kind === "signal" && !details.verification) notes.push("조건 판정 기록 · 거래소 주문/체결 확인 아님");
  if (typeof details.fees_known === "boolean") notes.push(sessionLogFieldNote("fees_known", details.fees_known));
  const positive = (value) => typeof value === "number" && Number.isFinite(value) && value > 0;
  const quantityKnown = positive(details.executed_qty);
  const priceKnown = positive(details.fill_price);
  // Keep old stored messages intact. An old command-success record without a
  // complete fill pair is not evidence that an order was sent (or not sent).
  if (item?.event_name === "command.execution_reported" && (!quantityKnown || !priceKnown)) {
    notes.push(!quantityKnown && !priceKnown
      ? "체결 수량·가격 확인 정보 없음 · 명령 성공 보고만 기록됨"
      : "체결 수량·가격 확인 정보 불완전 · 명령 성공 보고만 기록됨");
  }
  if (item?.event_name === "runner.order_result" && details.raw_status === "FILLED" && details.fees_known === false && !quantityKnown) {
    notes.push("체결 수량 확인 정보 없음 · FILLED 상태만으로 체결 확정 불가");
  }
  return notes;
}

// Defense in depth for copy/export. The owner API already sanitizes its payload;
// never copy a newly introduced secret field merely because it is JSON-shaped.
export function safeSessionLogData(value, depth = 0) {
  if (depth > 7) return "[깊이 제한]";
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value === "string") return value.slice(0, 4000)
    .replace(/\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{10,}|AIza[A-Za-z0-9_-]{20,})\b/g, "[비밀값 제외]")
    .replace(/\bBearer\s+\S+/gi, "Bearer [비밀값 제외]")
    .replace(/(?:postgres(?:ql)?|https?):\/\/[^\s/]+:[^\s@]+@[^\s]+/gi, "[인증 포함 주소 제외]");
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => safeSessionLogData(item, depth + 1));
  if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).slice(0, 80)
    .filter(([key]) => !/api[_-]?key|secret|password|authorization|token|cookie|database[_-]?url|private[_-]?key/i.test(key))
    .map(([key, item]) => [key.slice(0, 120), safeSessionLogData(item, depth + 1)]));
  return null;
}

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
  const recordedFraction = typeof value === "string" ? value.match(/\.(\d{1,6})/)?.[1] : null;
  const fraction = recordedFraction ? `.${recordedFraction}` : typeof value === "number" && parsed % 1000 !== 0
    ? `.${String(date.getUTCMilliseconds()).padStart(3, "0")}` : "";
  return `${date.getUTCFullYear()}.${pad(date.getUTCMonth() + 1)}.${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(date.getUTCSeconds())}${fraction} KST`;
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
    if (item?.event_name === "news.request_failed") return "뉴스 요청 실패";
    if (NEWS_ERROR_STATES.has(item?.details?.translation_status)) return "뉴스 번역·요약 오류";
    if (isSessionLogError(item)) return "뉴스 수집 오류";
    if (isBrowserNewsReceipt(item)) return "뉴스 응답 수신 확인";
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
  if (item?.source === "runner") return "실행기";
  if (item?.source === "execution") return "실행 기록";
  if (item?.source === "news") return "뉴스 서비스";
  if (item?.source === "news_receipt") return "뉴스 응답 수신 확인";
  return "서버 기록";
}
