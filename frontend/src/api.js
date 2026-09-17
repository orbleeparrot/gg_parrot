// Thin API client. Relative URLs work in dev (Vite proxy) and in prod
// (FastAPI serves the built SPA and the /api routes from one origin).
import { getToken } from "./lib/auth.js";
import { createRequestCoordinator } from "./lib/requestCoordinator.js";
import { withRequestTimeout } from "./lib/requestTimeout.js";
import { withGatewayRetry } from "./lib/requestRetry.js";
import { createBoardListCache } from "./lib/boardListCache.js";
import { invalidateLeaderboardCache } from "./lib/cacheEvents.js";
import { ACTIVITY_EVENT, shouldSignalActivity } from "./lib/notifications.js";

const BASE = "";
const RUNNER_SESSIONS_STREAM_PATH = "/api/me/runner/sessions/stream";
const NOTIFICATIONS_STREAM_PATH = "/api/me/notifications/stream";
const getRequests = createRequestCoordinator();
const boardLists = createBoardListCache();
const PUBLIC_READS = new Set([
  "/api/news/market", "/api/hot-coins", "/api/symbols", "/api/candles", "/api/candles/live",
  "/api/kimchi-premium", "/api/usdkrw", "/api/funding-rate", "/api/fear-greed", "/api/hangang-temp",
  "/api/whale-activity", "/api/runner/download/info", "/api/auth/google/config", "/api/backtest/limits",
  "/api/challenge/today",
]);
function publicRead(path, method) {
  const pathname = path.split("?")[0];
  return method === "GET" && (PUBLIC_READS.has(pathname) || /^\/api\/news\/coin\/[^/]+$/.test(pathname)
    || /^\/api\/macros\/[^/]+$/.test(pathname));
}
async function leaderboardMutation(promise) {
  const result = await promise;
  invalidateLeaderboardCache();
  return result;
}

function boardListPath(page, size, { sort = "new", q = "", field = "all" } = {}) {
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (sort && sort !== "new") params.set("sort", sort);
  if (q) params.set("q", q);
  if (q && field && field !== "all") params.set("field", field);
  return `/api/board/posts?${params}`;
}

async function boardMutation(promise) {
  const result = await promise;
  boardLists.invalidate();
  return result;
}

function websocketUrl(path) {
  const configuredBase = String(import.meta.env?.VITE_API_WS_BASE || "").trim();
  if (configuredBase) {
    const wsBase = configuredBase
      .replace(/^http:/i, "ws:")
      .replace(/^https:/i, "wss:")
      .replace(/\/+$/, "");
    return `${wsBase}${path}`;
  }

  if (import.meta.env?.DEV && typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${path}`;
  }

  return `wss://gg-parrot.onrender.com${path}`;
}
// SSE(EventSource)는 WebSocket 과 같은 이유로 Vercel 리라이트를 거치지 않고 백엔드에 바로 붙는다.
// 개발(Vite)에서는 같은 출처의 프록시를 쓰므로 상대 경로면 된다.
function eventStreamUrl(path) {
  const configuredBase = String(import.meta.env?.VITE_API_WS_BASE || "").trim();
  if (configuredBase) {
    return `${configuredBase.replace(/^ws:/i, "http:").replace(/^wss:/i, "https:").replace(/\/+$/, "")}${path}`;
  }
  if (import.meta.env?.DEV) return path;
  return `https://gg-parrot.onrender.com${path}`;
}
// 로그인 계정의 쓰기 요청이 끝났다 — 알림 배지가 폴링을 기다리지 않고 바로 다시 묻는다(useNotifications).
function signalActivity(path, method) {
  if (typeof window === "undefined" || typeof window.dispatchEvent !== "function") return;
  try {
    window.dispatchEvent(new CustomEvent(ACTIVITY_EVENT, { detail: { path, method } }));
  } catch (_) {
    // CustomEvent 가 없는 환경(테스트)에서는 조용히 넘어간다
  }
}

async function jsonBody(res) {
  const text = await res.text();
  const gatewayError = [502, 503, 504].includes(res.status);
  if (!text && !gatewayError) return {};
  try {
    return JSON.parse(text);
  } catch (_) {
    const error = new Error(gatewayError
      ? "서버에 일시적으로 연결하지 못했어요. 잠시 후 다시 시도해 주세요."
      : "서버가 API 대신 페이지를 반환했어요.");
    error.status = res.status;
    error.code = gatewayError ? "TEMPORARY_SERVER_ERROR" : "NON_JSON_RESPONSE";
    throw error;
  }
}

async function req(path, opts = {}) {
  const method = String(opts.method || "GET").toUpperCase();
  const shared = publicRead(path, method);
  const token = shared ? "" : getToken();
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const { signal: callerSignal, timeoutMs, requestKey = "", ...fetchOptions } = opts;
  const execute = (signal) => withRequestTimeout((requestSignal) => withGatewayRetry(async () => {
    const res = await fetch(BASE + path, { ...fetchOptions, cache: shared ? (fetchOptions.cache || "default") : "no-store", credentials: shared ? "omit" : "same-origin", method, headers, signal: requestSignal });
    const body = await jsonBody(res);
    if (!res.ok) {
      const detail = typeof body.detail === "string"
        ? body.detail
        : body.detail != null
          ? JSON.stringify(body.detail)
          : res.statusText;
      const error = new Error(detail);
      error.status = res.status;
      throw error;
    }
    if (token && shouldSignalActivity(method, path)) signalActivity(path, method);
    return body;
  }, { method, signal: requestSignal }), { signal, timeoutMs });
  if (method !== "GET") return execute(callerSignal);
  const authScope = token || "anonymous";
  return getRequests.run(`${authScope}:${requestKey}:${path}`, execute, { signal: callerSignal });
}

// multipart/form-data 요청 (파일 업로드). Content-Type은 브라우저가 boundary와
// 함께 자동 설정하도록 두고, Authorization 헤더만 붙인다.
async function reqForm(path, formData, options = {}) {
  const { method = "POST", ...requestOptions } = options;
  const token = getToken();
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return withRequestTimeout(async (signal) => {
    const res = await fetch(BASE + path, { method, headers, body: formData, signal, cache: "no-store" });
    const body = await jsonBody(res);
    if (!res.ok) {
      const detail = typeof body.detail === "string"
        ? body.detail
        : body.detail != null
          ? JSON.stringify(body.detail)
          : res.statusText;
      const error = new Error(detail);
      error.status = res.status;
      throw error;
    }
    return body;
  }, requestOptions);
}

export const api = {
  // account auth
  signup: (email, username, password) =>
    req("/api/auth/signup", { method: "POST", body: JSON.stringify({ email, username, password }) }),
  login: (email, password) =>
    req("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  // 구글 간편 로그인: 서버가 켜졌는지 + client_id 확인 후, GIS 가 준 credential 로 로그인/가입.
  googleConfig: () => req("/api/auth/google/config"),
  googleAuth: (credential) =>
    req("/api/auth/google", { method: "POST", body: JSON.stringify({ credential }) }),
  me: () => req("/api/auth/me"),
  // 관리자 대시보드(User.is_admin 계정만) — 유입·가입·매크로 지표와 뉴스 수집 현황
  adminOverview: (days = 30, options = {}) => req(`/api/admin/overview?days=${Number(days) || 30}`, { timeoutMs: 20_000, ...options }),
  adminNews: (options = {}) => req("/api/admin/news", { timeoutMs: 20_000, ...options }),
  myDashboard: (options = {}) => req("/api/me/dashboard", { timeoutMs: 15_000, ...options }),
  // 오늘(KST)의 일일 퀘스트 — 완료 여부·보상·오늘 번 포인트.
  myQuests: (options = {}) => req("/api/me/quests", options),
  // 알림(헤더 종): 목록 · 안 읽은 수 · 읽음 처리
  myNotifications: ({ after, ...options } = {}) =>
    req(`/api/me/notifications${Number.isFinite(Number(after)) && after !== undefined ? `?after=${Number(after)}` : ""}`, { timeoutMs: 10_000, ...options }),
  myNotificationsUnread: (options = {}) => req("/api/me/notifications/unread", { timeoutMs: 8_000, ...options }),
  readNotifications: ({ ids = [], all = false } = {}) =>
    req("/api/me/notifications/read", { method: "POST", body: JSON.stringify({ ids, all }) }),
  notificationsStreamToken: () => req("/api/me/notifications/stream-token", { method: "POST", timeoutMs: 8_000 }),
  notificationsStreamUrl: (token) => `${eventStreamUrl(NOTIFICATIONS_STREAM_PATH)}?token=${encodeURIComponent(token)}`,
  uploadAvatar: (image) => {
    const form = new FormData();
    form.append("image", image);
    return boardMutation(reqForm("/api/me/avatar", form, { timeoutMs: 30_000 }));
  },
  deleteAvatar: () => boardMutation(req("/api/me/avatar", { method: "DELETE", timeoutMs: 30_000 })),
  deleteAccount: ({ confirmation, password, credential }, options = {}) => req("/api/me/account", {
    ...options, method: "DELETE", body: JSON.stringify({ confirmation, password, credential }), timeoutMs: 30_000,
  }),
  updateProfile: ({ username, bio, image, removeAvatar = false }, options = {}) => {
    const form = new FormData();
    form.append("username", username);
    form.append("bio", bio || "");
    form.append("remove_avatar", String(removeAvatar));
    if (image) form.append("image", image);
    return boardMutation(reqForm("/api/me/profile", form, { ...options, method: "PATCH", timeoutMs: 30_000 }));
  },
  changePassword: ({ currentPassword, newPassword }, options = {}) =>
    req("/api/me/password", {
      ...options, method: "POST", timeoutMs: 30_000,
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
  myMacros: (options = {}) => req("/api/me/macros", options),
  myMacro: (id) => req(`/api/me/macros/${id}`),
  saveMyMacro: (macro, name = "") =>
    req("/api/me/macros", {
      method: "POST",
      body: JSON.stringify({ macro, name }),
    }),
  saveLeaderboardMacro: (entryId) =>
    req(`/api/me/macros/from-leaderboard/${entryId}`, { method: "POST" }),
  forgotPassword: (email) =>
    req("/api/auth/forgot", { method: "POST", body: JSON.stringify({ email }) }),
  resetPassword: (token, password) =>
    req("/api/auth/reset", { method: "POST", body: JSON.stringify({ token, password }) }),

  createMacro: (macro) => req("/api/macros", { method: "POST", body: JSON.stringify(macro) }),
  getMacro: (slug) => req(`/api/macros/${slug}`),
  backtestLimits: () => req("/api/backtest/limits", { timeoutMs: 8000, cache: "no-store" }),
  backtest: (macro, periodOverride) =>
    req("/api/backtest", {
      method: "POST",
      body: JSON.stringify({ macro, period_override: periodOverride || null }),
    }),
  // 껄무새 AI 원인 분석 (온디맨드). 서버 Gemini 키 사용. 키 없거나 실패 시
  // 규칙기반 해설 + ai_error 로 폴백해 응답.
  explainAi: (macro, periodOverride) =>
    req("/api/explain/ai", {
      method: "POST",
      body: JSON.stringify({ macro, period_override: periodOverride || null }),
    }),
  // parameter sweep (익절/손절 자동 최적화). tpValues/slValues optional (server defaults).
  optimize: (macro, tpValues, slValues) =>
    req("/api/optimize", {
      method: "POST",
      body: JSON.stringify({ macro, tp_values: tpValues || null, sl_values: slValues || null }),
    }),

  cardUrl: (slug) => `/api/card/${slug}.png`,
  // 거래 가능한 종목 목록(현물 + USDT-M 선물) — 조건 판의 종목 검색은 이 안에서만 고른다.
  symbols: (options = {}) => req("/api/symbols", { timeoutMs: 25_000, ...options }),
  coinLogoUrl: (base) => `/api/coin-logo/${encodeURIComponent(base)}.png`,

  // kimchi premium (reference indicator; upbit vs binance×USDKRW)
  kimchiPremium: (symbol, options = {}) => req(`/api/kimchi-premium?symbol=${encodeURIComponent(symbol || "BTC")}`, options),

  // approximate USD→KRW rate (reference only) for showing 원화 next to USDT amounts
  usdKrw: () => req("/api/usdkrw"),

  // average daily USDT-M funding cost (%) for the symbol/period (real futures data)
  fundingRate: (symbol, preset, start, end) => {
    const q = new URLSearchParams({ symbol, preset: preset || "1y" });
    if (start) q.set("start", start);
    if (end) q.set("end", end);
    return req(`/api/funding-rate?${q.toString()}`);
  },

  // 오늘의 AI 챌린지 (KST 하루 1회 생성; symbol + 🤖 이름)
  challengeToday: (options = {}) => req("/api/challenge/today", { timeoutMs: 10_000, ...options }),

  // '오늘의 경주마' hot coins (server-cached, shared across clients)
  hotCoins: (limit, options = {}) => req(`/api/hot-coins?limit=${limit || 10}`, options),

  // '오늘의 코인동향' — 시장·규제 뉴스 헤드라인 + AI 중립 개요 (KST 하루 1회 캐시)
  newsMarket: (options = {}) => req("/api/news/market", { timeoutMs: 10_000, ...options }),
  // '경주마 동향' — 서버가 Prefect DB 우선, 미수집 티커만 RSS fallback
  newsCoin: (symbol, options = {}) => req(`/api/news/coin/${encodeURIComponent(symbol)}`, { timeoutMs: 10_000, ...options }),
  // 내 에이전트 기능 01 — 서버가 세션 소유권과 등록 매크로 방향을 확인한다.
  agentWhaleActivity: (sessionId, options = {}) =>
    req(`/api/me/agents/sessions/${sessionId}/whale-activity`, options),
  agentPositionNews: (sessionId, { cursor, ...options } = {}) => {
    const query = Number.isSafeInteger(cursor) ? `?cursor=${cursor}` : "";
    return req(`/api/me/agents/sessions/${encodeURIComponent(sessionId)}/position-news${query}`, options);
  },
  // 저장 매크로도 실행 세션 없이 같은 공용 snapshot을 조회한다.
  agentMacroPositionNews: (macroId, symbol = "") => {
    const path = `/api/me/agents/macros/${encodeURIComponent(macroId)}/position-news`;
    const query = symbol
      ? `?symbol=${encodeURIComponent(symbol)}`
      : "";
    return req(`${path}${query}`);
  },

  // 껄무새 게시판
  subscribeBoardList: boardLists.subscribe,
  boardListVersion: boardLists.version,
  boardListCached: (page = 1, size = 10, options = {}) => boardLists.peek(`${getToken()}:${boardListPath(page, size, options)}`),
  boardList: (page = 1, size = 10, options = {}) => {
    const path = boardListPath(page, size, options);
    return boardLists.load(`${getToken()}:${path}`, version => req(path, { signal: options.signal, requestKey: `board-${version}` }));
  },
  boardVote: (id, value) => boardMutation(req(`/api/board/posts/${id}/vote`, { method: "POST", body: JSON.stringify({ value }) })),
  boardGet: (id, options = {}) => req(`/api/board/posts/${id}`, options).then(post => { boardLists.updatePost(post); return post; }),
  // 글 작성(로그인 필요) — title/body + 선택 이미지(File). multipart 전송.
  boardCreate: ({ title, body, bodyFormat = "text", images = [] }) => {
    const fd = new FormData();
    fd.append("title", title);
    fd.append("body", body || "");
    fd.append("body_format", bodyFormat);
    for (const file of images) fd.append("images", file);
    return boardMutation(reqForm("/api/board/posts", fd));
  },
  boardUpdate: (id, { title, body, bodyFormat = "text", keepImageIds = [], images = [] }) => {
    const fd = new FormData();
    fd.append("title", title);
    fd.append("body", body || "");
    fd.append("body_format", bodyFormat);
    fd.append("keep_image_ids", keepImageIds.join(","));
    for (const file of images) fd.append("images", file);
    return boardMutation(reqForm(`/api/board/posts/${id}`, fd, { method: "PUT" }));
  },
  boardDelete: (id) => boardMutation(req(`/api/board/posts/${id}`, { method: "DELETE" })),
  boardImageUrl: (id) => `/api/board/posts/${id}/image`,
  // 댓글·답글 — 로그인 계정으로 작성. 성공하면 관련 목록을 다시 조회한다.
  boardAddComment: (postId, text, parentId = null) =>
    boardMutation(req(`/api/board/posts/${postId}/comments`, { method: "POST", body: JSON.stringify({ text, parent_id: parentId }) })),
  boardEditComment: (commentId, text) =>
    boardMutation(req(`/api/board/comments/${commentId}`, { method: "PUT", body: JSON.stringify({ text }) })),
  boardDeleteComment: (commentId) => boardMutation(req(`/api/board/comments/${commentId}`, { method: "DELETE" })),
  boardReport: ({ targetType, targetId, reason, detail = "" }) =>
    req("/api/board/reports", { method: "POST", body: JSON.stringify({ target_type: targetType, target_id: targetId, reason, detail }) }),

  // 한강 수온 (server-cached proxy of the public Hangang temperature API)
  hangangTemp: (options = {}) => req("/api/hangang-temp", options),

  // 공포·탐욕 지수 (시장 전체 심리; 서버 캐시, Alternative.me 프록시)
  fearGreed: (options = {}) => req("/api/fear-greed", options),

  // Prefect가 저장한 공통 온체인 관측을 읽습니다. 이 요청은 외부 수집을 실행하지 않습니다.
  whaleActivity: () => req("/api/whale-activity"),

  // 실시간 봉차트용 최근 캔들 (서버 캐시; 마지막 봉은 진행 중이라 closed=false)
  candles: (symbol, interval, limit, market = "spot") =>
    req(
      `/api/candles?symbol=${encodeURIComponent(symbol)}` +
        `&interval=${encodeURIComponent(interval || "1m")}&limit=${limit || 120}` +
        `&market=${encodeURIComponent(market === "futures" ? "futures" : "spot")}`
    ),
  liveCandles: (symbol, interval, market = "spot") =>
    req(
      `/api/candles/live?symbol=${encodeURIComponent(symbol)}` +
        `&interval=${encodeURIComponent(interval || "1m")}` +
        `&market=${encodeURIComponent(market === "futures" ? "futures" : "spot")}`
    ),

  // 오늘의 리더보드 (daily KST paper-return board)
  leaderboard: (userId, options = {}) => {
    const { page = 1, pageSize = 50, snapshotId = "", entryId = null, ...requestOptions } = options;
    const query = new URLSearchParams({ user_id: userId || "", page: String(page), page_size: String(pageSize) });
    if (snapshotId) query.set("snapshot_id", snapshotId);
    if (entryId != null) query.set("entry_id", String(entryId));
    return req(`/api/leaderboard?${query}`, { timeoutMs: 10_000, ...requestOptions });
  },
  leaderboardAll: async (userId, options = {}) => {
    let snapshotId = "";
    const items = [];
    let result;
    for (let page = 1; page <= 100; page += 1) {
      result = await api.leaderboard(userId, { ...options, page, pageSize: 100, snapshotId });
      if (result.snapshot_expired) throw new Error("리더보드가 갱신됐어요. 목록을 다시 열어 주세요.");
      snapshotId = result.snapshot_id;
      items.push(...(result.items || []));
      if (!result.has_more) return { ...result, items };
    }
    throw new Error("목록이 너무 커요. 리더보드 페이지에서 참가자를 찾아 주세요.");
  },
  leaderboardRegister: (macro, username, password, userId, mode) =>
    leaderboardMutation(req("/api/leaderboard/register", {
      method: "POST",
      body: JSON.stringify({ macro, username, password, user_id: userId, mode: mode || "live" }),
    })),
  leaderboardEdit: (entryId, macro, password, mode) =>
    leaderboardMutation(req(`/api/leaderboard/${entryId}/edit`, {
      method: "POST",
      body: JSON.stringify({ macro, password: password || "", mode: mode || "live" }),
    })),
  // 계정 소유 엔트리 삭제 (로그인 필요, 소유자만).
  leaderboardDelete: (entryId) =>
    leaderboardMutation(req(`/api/leaderboard/${entryId}`, { method: "DELETE" })),
  leaderboardVote: (entryId, userId, value) =>
    req(`/api/leaderboard/${entryId}/vote`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, value }),
    }),
  // 포인트를 소진해 매크로 공개+복사 (창작자에게 70% 분배). 로그인 필요.
  leaderboardUnlock: (entryId) =>
    leaderboardMutation(req(`/api/leaderboard/${entryId}/unlock`, { method: "POST" })),

  // leaderboard chat (daily KST)
  chatList: ({ beforeId, seenId, afterId, metadataOnly, messageIds, ...options } = {}) => {
    const query = new URLSearchParams();
    if (beforeId != null) query.set("before_id", String(beforeId));
    if (seenId != null) query.set("seen_id", String(seenId));
    if (afterId != null) query.set("after_id", String(afterId));
    if (metadataOnly) query.set("metadata_only", "true");
    if (messageIds?.length) query.set("message_ids", messageIds.join(","));
    return req(`/api/chat${query.size ? `?${query}` : ""}`, { timeoutMs: 15_000, ...options });
  },
  chatPost: (text, options = {}) =>
    req("/api/chat", { timeoutMs: 15_000, ...options, method: "POST", body: JSON.stringify({ text }) }),
  chatRead: (lastSeenId, options = {}) =>
    req("/api/chat/read", { timeoutMs: 15_000, ...options, method: "PUT", body: JSON.stringify({ last_seen_id: lastSeenId }) }),

  // paper (simulated) trading
  paperStart: (macro, symbol, mode) =>
    req("/api/paper/start", { method: "POST", body: JSON.stringify({ macro, symbol, mode }) }),
  paperStop: (sessionId, options = {}) =>
    req(`/api/paper/${sessionId}/stop`, { method: "POST", keepalive: !!options.keepalive }),
  paperStatus: (sessionId, options = {}) => req(`/api/paper/${sessionId}`, options),

  // 매크로 실행기(exe) 다운로드
  runnerDownloadInfo: () => req("/api/runner/download/info"),
  runnerDownloadUrl: "/api/runner/download",

  // 매크로 실행기(exe) 연동 — 마이페이지용
  runnerKey: (options = {}) => req("/api/me/runner/key", options),
  runnerKeyRegenerate: () => req("/api/me/runner/key/regenerate", { method: "POST" }),
  runnerSessions: (options = {}) => req("/api/me/runner/sessions", options),
  // 세션 실행 로그(최신순) — 실행기가 heartbeat 로 올린 신호·주문·체결·오류.
  runnerSessionEvents: (sessionId, options = {}) =>
    req(`/api/me/runner/sessions/${encodeURIComponent(sessionId)}/events`, options),
  runnerSessionsStreamToken: () =>
    req("/api/me/runner/sessions/stream-token", { method: "POST" }),
  runnerSessionsStreamUrl: () => websocketUrl(RUNNER_SESSIONS_STREAM_PATH),
  runnerLaunchTicketIssue: (userMacroId, testnet = true) =>
    req("/api/me/runner/launch-tickets", {
      method: "POST",
      body: JSON.stringify({ user_macro_id: userMacroId, testnet: !!testnet }),
    }),
  runnerLaunchTicketStatus: (launchId) =>
    req(`/api/me/runner/launch-tickets/${encodeURIComponent(launchId)}`),
  runnerLaunchTicketClaim: (ticket, runnerVersion = "5") =>
    req("/api/runner/launch-tickets/claim", {
      method: "POST",
      body: JSON.stringify({ ticket, runner_version: runnerVersion }),
    }),
  // mode: "stop_only"(매크로만) | "close_and_stop"(청산 후 종료)
  // 응답이 끊겼거나 이미 끝난 세션만 지울 수 있다(살아 있는 실행은 409).
  runnerDeleteSession: (sessionId) =>
    req(`/api/me/runner/sessions/${sessionId}`, { method: "DELETE" }),
  runnerRequestStop: (sessionId, mode) =>
    req(`/api/me/runner/sessions/${sessionId}/request-stop`, {
      method: "POST",
      body: JSON.stringify({ mode }),
    }),

  // 매크로 파일(.ggm.json) 하나만 내려받기 — 실행기에 넣어서 구동한다.
  async downloadMacroFile(macro) {
    const res = await fetch("/api/realtrade/macro-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ macro }),
    });
    if (!res.ok) throw new Error("매크로 파일 생성 실패");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `macro-${macro.rule_type}-${macro.position_side}.ggm.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },

  // real-trade executable bundle (레거시 zip: bot.py+run.bat). 실행기 방식으로
  // 전환하면서 남겨둔 하위호환 다운로드.
  async downloadBundle(macro) {
    const res = await fetch("/api/realtrade/bundle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ macro }),
    });
    if (!res.ok) throw new Error("번들 생성 실패");
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `realtrade-bot-${macro.rule_type}-${macro.position_side}.zip`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  },
};
