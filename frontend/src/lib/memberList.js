// 회원 관리 탭의 순수 계산 — 주소(?tab=members&page&q&status) 파싱·직렬화, 페이지 수, 상태·가입 방법 라벨,
// 조치 가능 여부, 결과 한 줄 문구. JSX 가 없는 것만 여기 둔다(node --test 가 JSX 를 못 읽는다).
// 서버 계약: GET /api/admin/members (page · page_size · q · status · sort).
import { DASH } from "./adminFormat.js";

// 상태 필터 칩 — counts 의 키와 같은 순서·같은 이름.
export const MEMBER_STATUSES = [
  { key: "all", label: "전체" },
  { key: "active", label: "활동" },
  { key: "blocked", label: "차단" },
  { key: "deleted", label: "탈퇴" },
  { key: "admin", label: "관리자" },
];
export const MEMBER_STATUS_KEYS = MEMBER_STATUSES.map((s) => s.key);
export const MEMBER_SORTS = ["recent", "oldest", "points", "username"];
export const MEMBER_PAGE_SIZES = [25, 50, 100];
export const MEMBER_Q_MAX = 60;
export const MEMBER_REASON_MAX = 200;
export const MEMBER_TITLE_MAX = 80;
export const MEMBER_BODY_MAX = 1000;
export const MEMBER_LINK_MAX = 200;
export const DEFAULT_MEMBER_QUERY = { page: 1, pageSize: 25, q: "", status: "all", sort: "recent" };

export function memberStatusLabel(key) {
  const found = MEMBER_STATUSES.find((s) => s.key === key);
  return found ? found.label : MEMBER_STATUSES[0].label;
}

// 가입 방법은 표에 들어가므로 짧게 — 용어 표에 "구글 간편 가입 · 이메일 가입" 을 적어 둔다.
// unknown = 탈퇴 행처럼 서버가 가입 방법을 추정하지 않는 계정(코드 그대로 "unknown" 이 찍히지 않게).
export const SIGNUP_LABELS = { google: "구글", email: "이메일", unknown: "알 수 없음" };

export function memberSignup(member) {
  const code = String(member?.signup_method ?? "").trim();
  return SIGNUP_LABELS[code] || code || DASH;
}

// 이메일은 서버가 마스킹해서 준다(a***@gmail.com). 빈 값이면 0 도 빈칸도 아닌 "—".
export function memberEmail(member) {
  return String(member?.email_masked ?? "").trim() || DASH;
}

export function memberTier(member) {
  return String(member?.tier_name ?? "").trim() || DASH;
}

// 상태 알약 — 탈퇴가 차단을 덮는다(탈퇴한 계정의 차단 여부는 뜻이 없다). 관리자는 별도 배지라 여기서 안 다룬다.
export function memberState(member) {
  if (member?.is_deleted) return { key: "deleted", tone: "off", label: "탈퇴" };
  if (member?.is_blocked) return { key: "blocked", tone: "bad", label: "차단" };
  return { key: "active", tone: "ok", label: "활동" };
}

// 행마다 어떤 버튼을 보여 줄지 — 서버가 400 으로 막는 버튼은 화면에도 두지 않는다(2026-09-18 확정).
// 탈퇴한 계정은 전부 숨김 · 관리자 계정은 차단·탈퇴가 막히지만 메시지는 보낼 수 있다 · 자기 계정은 아무것도 못 한다.
export function memberActions(member, selfId = null) {
  const none = { message: false, block: false, remove: false };
  if (!member || member.is_deleted) return none;
  if (selfId != null && String(member.id) === String(selfId)) return none;
  if (member.is_admin) return { message: true, block: false, remove: false };
  return { message: true, block: true, remove: true };
}

export function canActOnMember(member, selfId = null) {
  const allowed = memberActions(member, selfId);
  return allowed.message || allowed.block || allowed.remove;
}

function readParam(source, key) {
  if (!source) return null;
  if (typeof source.get === "function") return source.get(key);
  return source[key];
}

function intOr(value, fallback) {
  const n = Number.parseInt(String(value ?? ""), 10);
  return Number.isFinite(n) ? n : fallback;
}

// 주소가 곧 상태 — 새로 고쳐도 같은 쪽·같은 검색어·같은 필터가 남는다. 이상한 값은 기본값으로 접는다.
export function parseMemberQuery(source) {
  const page = Math.max(1, intOr(readParam(source, "page"), 1));
  const rawSize = intOr(readParam(source, "size"), DEFAULT_MEMBER_QUERY.pageSize);
  const pageSize = MEMBER_PAGE_SIZES.includes(rawSize) ? rawSize : DEFAULT_MEMBER_QUERY.pageSize;
  const q = String(readParam(source, "q") ?? "").trim().slice(0, MEMBER_Q_MAX);
  const status = String(readParam(source, "status") ?? "");
  const sort = String(readParam(source, "sort") ?? "");
  return {
    page,
    pageSize,
    q,
    status: MEMBER_STATUS_KEYS.includes(status) ? status : DEFAULT_MEMBER_QUERY.status,
    sort: MEMBER_SORTS.includes(sort) ? sort : DEFAULT_MEMBER_QUERY.sort,
  };
}

// 주소용 — 기본값(1쪽 · 25개 · 검색 없음 · 전체 · 최신순)은 주소에서 뺀다(게시판과 같은 규칙).
export function memberSearchParams(query, { tab = "members" } = {}) {
  const q = parseMemberQuery({ ...DEFAULT_MEMBER_QUERY, ...query, size: query?.pageSize });
  const params = new URLSearchParams({ tab });
  if (q.page > 1) params.set("page", String(q.page));
  if (q.q) params.set("q", q.q);
  if (q.status !== DEFAULT_MEMBER_QUERY.status) params.set("status", q.status);
  if (q.pageSize !== DEFAULT_MEMBER_QUERY.pageSize) params.set("size", String(q.pageSize));
  if (q.sort !== DEFAULT_MEMBER_QUERY.sort) params.set("sort", q.sort);
  return params;
}

// API 용 — 서버는 page · page_size 를 항상 받고, 나머지는 기본값이면 생략한다.
export function memberQueryString(query) {
  const q = parseMemberQuery({ ...DEFAULT_MEMBER_QUERY, ...query, size: query?.pageSize });
  const params = new URLSearchParams({ page: String(q.page), page_size: String(q.pageSize) });
  if (q.q) params.set("q", q.q);
  if (q.status !== DEFAULT_MEMBER_QUERY.status) params.set("status", q.status);
  if (q.sort !== DEFAULT_MEMBER_QUERY.sort) params.set("sort", q.sort);
  return params.toString();
}

// 총 건수 → 쪽 수. 0 건도 1쪽("1 / 1 페이지")으로 둔다 — 0쪽은 읽는 사람에게 뜻이 없다.
export function pageCount(total, pageSize) {
  const n = Number(total);
  const size = Number(pageSize);
  if (!Number.isFinite(n) || n <= 0 || !Number.isFinite(size) || size <= 0) return 1;
  return Math.max(1, Math.ceil(n / size));
}

export function clampPage(page, pages) {
  const n = Number.parseInt(String(page ?? ""), 10);
  const last = Math.max(1, Number.parseInt(String(pages ?? "1"), 10) || 1);
  if (!Number.isFinite(n) || n < 1) return 1;
  return Math.min(n, last);
}

// 조치 결과는 토스트가 아니라 표 위 한 줄 — 무엇이 달라졌는지까지 적는다(용어 표와 같은 말로).
export function memberResultLine(kind, member) {
  const name = String(member?.username ?? "").trim() || "회원";
  if (kind === "message") return `${name} 님에게 메시지를 보냈어요.`;
  if (kind === "block") return `${name} 님을 차단했어요 — 채팅·게시글·댓글을 쓸 수 없어요(로그인·열람·백테스트는 그대로).`;
  if (kind === "unblock") return `${name} 님의 차단을 해제했어요.`;
  if (kind === "delete") return `${name} 님을 탈퇴 처리했어요 — 같은 이메일로 다시 가입할 수 없어요.`;
  return `${name} 님에게 조치했어요.`;
}

// 차단/해제는 같은 버튼이라 어느 쪽을 눌렀는지로 결과 문구가 갈린다.
export function blockActionKind(member) {
  return member?.is_blocked ? "unblock" : "block";
}

const MEMBER_ERROR_FALLBACK = {
  400: "처리할 수 없는 요청이에요.",
  401: "다시 로그인해 주세요.",
  403: "관리자만 할 수 있어요.",
  404: "회원을 찾을 수 없어요.",
  409: "실행 중인 매크로를 먼저 종료해 주세요.",
};

// 404 · 400 · 409 는 서버가 한글 detail 을 준다(api.js 가 Error.message 로 옮긴다) — 그 문장을 그대로 보여 준다.
export function memberActionError(error) {
  const message = String(error?.message ?? "").trim();
  if (message) return message;
  return MEMBER_ERROR_FALLBACK[Number(error?.status)] || "조치하지 못했어요.";
}
