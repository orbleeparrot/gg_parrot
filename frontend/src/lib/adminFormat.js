// 관리자 대시보드 표기 — 숫자·비율·시간·상대시각과 서버 코드 → 한글 라벨.
// 서버는 값만 준다(ms epoch · % 숫자 · 코드 문자열). 화면에 찍히는 문자열은 전부 여기서 만들어야
// 다섯 탭이 같은 자릿수·같은 단위로 읽힌다. 값이 없으면(null/undefined/NaN) 언제나 "—" —
// 집계가 없는 칸에 0 을 지어내지 않는다(계약: 가짜 숫자 금지).

export const AGGREGATION_START = "2026-09-17";
export const EMPTY_NOTE = `아직 데이터 없음 · 집계 시작 ${AGGREGATION_START}`;
export const DASH = "—";

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

export function fmtInt(value) {
  const n = finite(value);
  return n == null ? DASH : Math.round(n).toLocaleString("ko-KR");
}

export function fmtNum(value, digits = 1) {
  const n = finite(value);
  return n == null ? DASH : n.toLocaleString("ko-KR", { minimumFractionDigits: 0, maximumFractionDigits: digits });
}

export function fmtPct(value, digits = 1) {
  const n = finite(value);
  return n == null ? DASH : `${n.toFixed(digits)}%`;
}

// 수익률처럼 부호가 결론인 값 — 0 은 부호 없이.
export function fmtSignedPct(value, digits = 1) {
  const n = finite(value);
  if (n == null) return DASH;
  return `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function fmtUsd(value, digits = 2) {
  const n = finite(value);
  if (n == null) return DASH;
  const abs = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return `${n < 0 ? "-" : ""}$${abs}`;
}

// 토큰 수는 천 단위 콤마로는 안 읽힌다 — 38.2M / 412K 로 줄인다.
export function fmtTokens(value) {
  const n = finite(value);
  if (n == null) return DASH;
  const abs = Math.abs(n);
  if (abs >= 1e6) return `${(n / 1e6).toFixed(abs >= 1e7 ? 1 : 2)}M`;
  if (abs >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return Math.round(n).toLocaleString("ko-KR");
}

// 세션 시간·체류 시간. 초는 두 자리로 맞춰 열이 흔들리지 않게 한다("4분 02초").
export function fmtDuration(seconds) {
  const n = finite(seconds);
  if (n == null) return DASH;
  const total = Math.max(0, Math.round(n));
  if (total >= 3600) return `${Math.floor(total / 3600)}시간 ${Math.floor((total % 3600) / 60)}분`;
  return `${Math.floor(total / 60)}분 ${String(total % 60).padStart(2, "0")}초`;
}

// 과거 시각. 0/없음은 "—" — 한 번도 안 돈 엔진을 "56년 전" 으로 찍지 않기 위해.
export function fmtRelative(ms, now = Date.now()) {
  const n = finite(ms);
  if (!n) return DASH;
  const diff = now - n;
  if (diff < 45_000) return "지금";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}분 전`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}시간 전`;
  return `${Math.round(diff / 86_400_000)}일 전`;
}

// 미래 시각(다음 시도 · 탐침).
export function fmtUntil(ms, now = Date.now()) {
  const n = finite(ms);
  if (!n) return DASH;
  const diff = n - now;
  if (diff <= 0) return "지금";
  if (diff < 60_000) return `${Math.round(diff / 1000)}초 뒤`;
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}분 뒤`;
  return `${Math.round(diff / 3_600_000)}시간 뒤`;
}

function kstParts(input) {
  const date = typeof input === "number" ? new Date(input) : new Date(String(input || ""));
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", hourCycle: "h23",
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit",
  }).formatToParts(date);
  const pick = (type) => parts.find((p) => p.type === type)?.value || "00";
  return { y: pick("year"), mo: pick("month"), d: pick("day"), h: pick("hour"), mi: pick("minute"), s: pick("second") };
}

// ISO 문자열 또는 ms → "YYYY-MM-DD HH:mm" (KST). 서버 generated_at 은 UTC ISO 라 여기서 시간대를 맞춘다.
export function fmtKst(input) {
  const p = kstParts(input);
  return p ? `${p.y}-${p.mo}-${p.d} ${p.h}:${p.mi}` : DASH;
}

export function fmtTimeKst(ms) {
  const n = finite(ms);
  const p = n ? kstParts(n) : null;
  return p ? `${p.h}:${p.mi}` : DASH;
}

export function fmtDayTimeKst(ms) {
  const n = finite(ms);
  const p = n ? kstParts(n) : null;
  return p ? `${p.mo}-${p.d} ${p.h}:${p.mi}` : DASH;
}

export function fmtStamp(iso) {
  const stamp = fmtKst(iso);
  return stamp === DASH ? "갱신 시각 없음 · 1분마다 갱신" : `${stamp} KST · 1분마다 갱신`;
}

// "2026-09-01" → "09-01" (표) / "9/1" (차트 눈금).
export function fmtShortDay(day) {
  const s = String(day || "");
  return /^\d{4}-\d{2}-\d{2}$/.test(s) ? s.slice(5) : (s || DASH);
}

export function fmtDateTick(day) {
  const s = String(day || "");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return s;
  return `${Number(s.slice(5, 7))}/${Number(s.slice(8, 10))}`;
}

// "2026-09" → "9월", 진행 중인 달은 "9월*" (용어 표가 * 를 설명한다).
export function fmtMonthLabel(month, { current = false } = {}) {
  const s = String(month || "");
  const m = /^\d{4}-(\d{2})$/.test(s) ? `${Number(s.slice(5, 7))}월` : (s || DASH);
  return current ? `${m}*` : m;
}

// 일일 한도: 숫자면 천 단위, 문자열이면 그대로, 없으면 "없음".
export function fmtLimit(value) {
  if (value === null || value === undefined || value === "") return "없음";
  const n = finite(value);
  return n == null ? String(value) : fmtInt(n);
}

// 분자 ÷ 분모 × 100. 분모 0 은 "—" (서버는 0 으로 주지만 합계 행에서 0% 는 오해를 부른다).
export function ratioPct(numerator, denominator) {
  const a = finite(numerator);
  const b = finite(denominator);
  if (a == null || !b) return null;
  return (a / b) * 100;
}

export function sumBy(rows, key) {
  return (rows || []).reduce((acc, row) => acc + (finite(row?.[key]) || 0), 0);
}

// 값이 하나도 없는 열의 합계 — sumBy 는 0 을 돌려줘 "0" 으로 찍힌다(가입 당일 백테스트 · 노출처럼 집계 시작 전
// 날짜가 전부 null 인 창). 유한한 값을 한 번도 못 봤으면 null → 화면은 "—". 0 이 실제로 온 행은 값으로 센다.
export function sumOrNull(rows, key) {
  let acc = null;
  for (const row of rows || []) {
    const v = finite(row?.[key]);
    if (v == null) continue;
    acc = (acc ?? 0) + v;
  }
  return acc;
}

// 값이 있는 행만의 단순 평균(일별 평균의 평균). 가중치가 될 분모(측정된 세션 수)를 서버가 주지 않을 때 —
// 세션 수로 가중하면 측정 안 된 세션까지 분모에 넣는 셈이라 가짜 가중이 된다. 값이 없으면 null.
export function meanBy(rows, key) {
  let sum = 0;
  let n = 0;
  for (const row of rows || []) {
    const v = finite(row?.[key]);
    if (v == null) continue;
    sum += v;
    n += 1;
  }
  return n ? sum / n : null;
}

// 이탈률·평균 세션처럼 행마다 비율인 값의 합계 행 — 세션 수로 가중 평균. 가중치 합 0 이면 null.
export function weightedMean(rows, key, weightKey) {
  let num = 0;
  let den = 0;
  for (const row of rows || []) {
    const v = finite(row?.[key]);
    const w = finite(row?.[weightKey]);
    if (v == null || !w) continue;
    num += v * w;
    den += w;
  }
  return den ? num / den : null;
}

export function isAllZero(values) {
  if (!Array.isArray(values) || values.length === 0) return true;
  return values.every((v) => !(finite(v) > 0));
}

export const CHANNEL_LABELS = {
  direct: "직접 접속", search: "검색", referral: "추천 링크", social: "소셜", campaign: "캠페인 (utm)",
};
export const CHANNEL_DETAIL = {
  direct: "직접 접속 (주소 · 북마크 · 앱)", search: "검색 (google · naver · bing)", referral: "추천 링크 (다른 사이트)",
  social: "소셜 (X · 카카오 · 유튜브)", campaign: "캠페인 (utm)",
};
// unknown = 화면 너비 0 으로 온 세션(비율 분모에서 빠지지만 행은 보여 준다).
export const DEVICE_LABELS = { mobile: "모바일", desktop: "데스크톱", tablet: "태블릿", unknown: "알 수 없음" };
// unknown = 탈퇴 행처럼 가입 방법을 추정하지 않는 계정.
export const METHOD_LABELS = { google: "구글 간편 가입", email: "이메일 가입", unknown: "알 수 없음" };
export const COST_METHOD_LABELS = { estimate: "추정", calls: "호출 × 단가", fixed: "구독", api: "청구 API" };
// 서버는 degraded/source_unavailable 을 error 로 접어 주지만, 원값이 그대로 와도 '소스 실패'로 읽히게 둔다.
export const ENGINE_STATUS = {
  ok: { tone: "ok", label: "정상" }, delayed: { tone: "wait", label: "지연" }, error: { tone: "bad", label: "오류" },
  stalled: { tone: "wait", label: "정체 쉼" }, idle: { tone: "off", label: "대기" }, skipped: { tone: "off", label: "건너뜀" },
  degraded: { tone: "bad", label: "소스 실패" }, source_unavailable: { tone: "bad", label: "소스 실패" },
};

// "집계 시작 YYYY-MM-DD" 캡션 — coverage 가 null(표가 빔)이면 날짜를 지어내지 않는다.
export function sinceNote(day, what = "집계 시작") {
  const s = String(day || "");
  return /^\d{4}-\d{2}-\d{2}$/.test(s) ? `${what} ${s}` : `${what} 전 (기록 없음)`;
}
export const BOARD_STATUS = { ok: "정상", empty: "비어 있음", bad: "실패", wait: "수집 중" };
export const PURPOSE_LABELS = {
  position_news: "종목 뉴스 분류 · 요약", title_translation: "뉴스 제목 한글 번역", community_summaries: "커뮤니티 글 요약",
  ai_explain: "백테스트 AI 해설", market_news_summary: "시장 브리핑 요약", ai_challenge: "일일 챌린지 생성",
};
export const PAGE_LABELS = {
  "/": "홈", "/leaderboard": "리더보드", "/builder": "직접 만들기", "/s/:id": "직접 만들기", "/news": "코인동향",
  "/board": "게시판", "/board/:id": "게시글", "/agents": "내 에이전트", "/mypage": "내 활동", "/mypage/settings": "프로필 설정",
  "/login": "로그인", "/runner/install": "실행기 설치", "/guide": "FAQ", "/support": "고객센터", "/admin": "관리자",
};

// 서버 label 이 우선, 없으면 코드 → 한글, 그것도 없으면 코드 그대로(빈 칸보다 낫다).
export function labelOf(map, key, serverLabel = "") {
  if (serverLabel) return serverLabel;
  const code = String(key ?? "");
  return map[code] || code || DASH;
}
