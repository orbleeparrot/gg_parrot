// Thin API client. Relative URLs work in dev (Vite proxy) and in prod
// (FastAPI serves the built SPA and the /api routes from one origin).
import { getToken } from "./lib/auth.js";

const BASE = "";
const RUNNER_SESSIONS_STREAM_PATH = "/api/me/runner/sessions/stream";

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

async function jsonBody(res) {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch (_) {
    const error = new Error("서버가 API 대신 페이지를 반환했어요.");
    error.status = res.status;
    error.code = "NON_JSON_RESPONSE";
    throw error;
  }
}

async function req(path, opts = {}) {
  const token = getToken();
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(BASE + path, { ...opts, headers });
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
}

// multipart/form-data 요청 (파일 업로드). Content-Type은 브라우저가 boundary와
// 함께 자동 설정하도록 두고, Authorization 헤더만 붙인다.
async function reqForm(path, formData) {
  const token = getToken();
  const headers = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  const res = await fetch(BASE + path, { method: "POST", headers, body: formData });
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
  myDashboard: () => req("/api/me/dashboard"),
  myMacros: () => req("/api/me/macros"),
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
  backtest: (macro, periodOverride) =>
    req("/api/backtest", {
      method: "POST",
      body: JSON.stringify({ macro, period_override: periodOverride || null }),
    }),
  // 껄무새 AI 원인 분석 (온디맨드). 서버 Anthropic 키 사용. 키 없거나 실패 시
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

  // kimchi premium (reference indicator; upbit vs binance×USDKRW)
  kimchiPremium: (symbol) => req(`/api/kimchi-premium?symbol=${encodeURIComponent(symbol || "BTC")}`),

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
  challengeToday: () => req("/api/challenge/today"),

  // '오늘의 경주마' hot coins (server-cached, shared across clients)
  hotCoins: (limit) => req(`/api/hot-coins?limit=${limit || 10}`),

  // '오늘의 코인동향' — 시장·규제 뉴스 헤드라인 + AI 중립 개요 (KST 하루 1회 캐시)
  newsMarket: () => req("/api/news/market"),
  // '경주마 동향' — 코인별 최신 뉴스 헤드라인
  newsCoin: (symbol) => req(`/api/news/coin/${encodeURIComponent(symbol)}`),
  // 내 에이전트 기능 01 — 서버가 세션 소유권과 등록 매크로 방향을 확인한다.
  agentPositionNews: (sessionId) =>
    req(`/api/me/agents/sessions/${encodeURIComponent(sessionId)}/position-news`),

  // 껄무새 게시판
  boardList: (page = 1, size = 10) => req(`/api/board/posts?page=${page}&size=${size}`),
  boardGet: (id) => req(`/api/board/posts/${id}`),
  // 글 작성(로그인 필요) — title/body + 선택 이미지(File). multipart 전송.
  boardCreate: ({ title, body, image }) => {
    const fd = new FormData();
    fd.append("title", title);
    fd.append("body", body || "");
    if (image) fd.append("image", image);
    return reqForm("/api/board/posts", fd);
  },
  boardDelete: (id) => req(`/api/board/posts/${id}`, { method: "DELETE" }),
  boardImageUrl: (id) => `/api/board/posts/${id}/image`,
  // 댓글 — 계정 없이 일회성 이름+비밀번호
  boardAddComment: (postId, { username, password, text }) =>
    req(`/api/board/posts/${postId}/comments`, {
      method: "POST",
      body: JSON.stringify({ username, password, text }),
    }),
  boardDeleteComment: (commentId, password) =>
    req(`/api/board/comments/${commentId}`, {
      method: "DELETE",
      body: JSON.stringify({ password }),
    }),

  // 한강 수온 (server-cached proxy of the public Hangang temperature API)
  hangangTemp: () => req("/api/hangang-temp"),

  // 공포·탐욕 지수 (시장 전체 심리; 서버 캐시, Alternative.me 프록시)
  fearGreed: () => req("/api/fear-greed"),

  // [차후 도입] '고래 동향' — 서버 라우트가 아직 꺼져 있어 지금 호출하면 404 입니다.
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
  leaderboard: (userId) => req(`/api/leaderboard?user_id=${encodeURIComponent(userId || "")}`),
  leaderboardRegister: (macro, username, password, userId, mode) =>
    req("/api/leaderboard/register", {
      method: "POST",
      body: JSON.stringify({ macro, username, password, user_id: userId, mode: mode || "live" }),
    }),
  leaderboardEdit: (entryId, macro, password, mode) =>
    req(`/api/leaderboard/${entryId}/edit`, {
      method: "POST",
      body: JSON.stringify({ macro, password: password || "", mode: mode || "live" }),
    }),
  // 계정 소유 엔트리 삭제 (로그인 필요, 소유자만).
  leaderboardDelete: (entryId) =>
    req(`/api/leaderboard/${entryId}`, { method: "DELETE" }),
  leaderboardVote: (entryId, userId, value) =>
    req(`/api/leaderboard/${entryId}/vote`, {
      method: "POST",
      body: JSON.stringify({ user_id: userId, value }),
    }),
  // 포인트를 소진해 매크로 공개+복사 (창작자에게 70% 분배). 로그인 필요.
  leaderboardUnlock: (entryId) =>
    req(`/api/leaderboard/${entryId}/unlock`, { method: "POST" }),

  // leaderboard chat (daily KST)
  chatList: () => req("/api/chat"),
  chatPost: (username, text) =>
    req("/api/chat", { method: "POST", body: JSON.stringify({ username, text }) }),

  // paper (simulated) trading
  paperStart: (macro, symbol, mode) =>
    req("/api/paper/start", { method: "POST", body: JSON.stringify({ macro, symbol, mode }) }),
  paperStop: (sessionId, options = {}) =>
    req(`/api/paper/${sessionId}/stop`, { method: "POST", keepalive: !!options.keepalive }),
  paperStatus: (sessionId) => req(`/api/paper/${sessionId}`),

  // 매크로 실행기(exe) 다운로드
  runnerDownloadInfo: () => req("/api/runner/download/info"),
  runnerDownloadUrl: "/api/runner/download",

  // 매크로 실행기(exe) 연동 — 마이페이지용
  runnerKey: () => req("/api/me/runner/key"),
  runnerKeyRegenerate: () => req("/api/me/runner/key/regenerate", { method: "POST" }),
  runnerSessions: () => req("/api/me/runner/sessions"),
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
