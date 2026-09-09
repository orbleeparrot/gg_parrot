// 내 활동(프로필) 화면의 글자 규칙 — 시각·포인트·등급 사다리·포인트 내역 사유.
// 화면은 여기서 만든 문자열을 그대로 놓는다. 규칙이 바뀌면 이 파일과 테스트만 고친다.

const KST_OFFSET_MS = 9 * 60 * 60 * 1000;

function kstParts(ms) {
  const d = new Date(ms + KST_OFFSET_MS);
  return { y: d.getUTCFullYear(), m: d.getUTCMonth() + 1, d: d.getUTCDate(), hh: d.getUTCHours(), mm: d.getUTCMinutes() };
}
const two = (n) => String(n).padStart(2, "0");

/** ISO("2026-08-12T05:00:00Z") 또는 ms → ms. 못 읽으면 null. */
export function toMs(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value) {
    const ms = Date.parse(value);
    if (Number.isFinite(ms)) return ms;
  }
  return null;
}

/** 목록의 시각 — 올해면 `MM.DD HH:MM`, 그 전이면 `YY.MM.DD` (KST). */
export function stampKst(value, nowMs = Date.now()) {
  const ms = toMs(value);
  if (ms == null) return "";
  const t = kstParts(ms);
  const now = kstParts(nowMs);
  if (t.y === now.y) return `${two(t.m)}.${two(t.d)} ${two(t.hh)}:${two(t.mm)}`;
  return `${String(t.y).slice(2)}.${two(t.m)}.${two(t.d)}`;
}

/** 툴팁·datetime 용 전체 시각 `YYYY.MM.DD HH:MM` (KST). */
export function fullKst(value) {
  const ms = toMs(value);
  if (ms == null) return "";
  const t = kstParts(ms);
  return `${t.y}.${two(t.m)}.${two(t.d)} ${two(t.hh)}:${two(t.mm)}`;
}

/** 가입일 한 줄 — `2026.08.12 가입`. 값이 없으면 빈 문자열. */
export function joinedLabel(value) {
  const ms = toMs(value);
  if (ms == null) return "";
  const t = kstParts(ms);
  return `${t.y}.${two(t.m)}.${two(t.d)} 가입`;
}

/** `1,240P`. 없으면 `0P`. */
export function formatPoints(n) {
  return `${Math.trunc(Number(n) || 0).toLocaleString("ko-KR")}P`;
}

/** 변동분 — `+70P` / `−100P` (부호 필수, 마이너스는 U+2212). 0 은 `0P`. */
export function signedPoints(delta) {
  const n = Math.trunc(Number(delta) || 0);
  if (n > 0) return `+${formatPoints(n)}`;
  if (n < 0) return `−${formatPoints(-n)}`;
  return formatPoints(0);
}

export const REASON_KO = {
  signup_grant: "가입 보너스",
  unlock_spend: "매크로 언락",
  unlock_earn: "판매 수익",
  topup: "충전",
};

/** 포인트 내역 사유 — 아는 사유는 우리말, 모르는 사유는 원문 그대로. */
export function reasonLabel(reason) {
  return REASON_KO[reason] || String(reason || "");
}

/** `entry:123` → 123. 아니면 null. */
export function refEntryId(ref) {
  const m = /^entry:(\d+)$/.exec(String(ref || ""));
  return m ? Number(m[1]) : null;
}

/** 포인트 내역 한 줄의 내용 — 사유 뒤에 관련 종목을 붙인다(`매크로 언락 · BTCUSDT`). */
export function ledgerLabel(row, symbolByEntry = {}) {
  const base = reasonLabel(row?.reason);
  const id = refEntryId(row?.ref);
  const symbol = id != null ? symbolByEntry[id] : "";
  return symbol ? `${base} · ${symbol}` : base;
}

// 백엔드 `_TIERS` 의 거울 — 사다리를 안 주는 옛 응답에서만 쓴다.
const LADDER_FALLBACK = [
  { name: "새싹", at: 0 },
  { name: "브론즈", at: 1 },
  { name: "실버", at: 5 },
  { name: "골드", at: 15 },
  { name: "다이아", at: 40 },
];

/** 등급 사다리 — 각 칸에 `done | current | todo` 상태를 붙인다. */
export function tierSteps(tier) {
  const ladder = Array.isArray(tier?.ladder) && tier.ladder.length ? tier.ladder : LADDER_FALLBACK;
  const sales = Number(tier?.sales) || 0;
  let currentIndex = 0;
  ladder.forEach((step, i) => { if (sales >= step.at) currentIndex = i; });
  return ladder.map((step, i) => ({
    name: step.name,
    at: step.at,
    state: i < currentIndex ? "done" : i === currentIndex ? "current" : "todo",
  }));
}

/** 사다리 아래 한 줄 — `실버까지 판매 2건 남았어요` / `가장 높은 등급이에요`. */
export function tierNextLabel(tier) {
  if (!tier?.next_name) return "가장 높은 등급이에요";
  const left = Math.max(0, Number(tier.to_next) || 0);
  return `${tier.next_name}까지 판매 ${left.toLocaleString("ko-KR")}건 남았어요`;
}

/** 사다리 칸의 조건 글자 — 시작 칸은 `시작`, 나머지는 `판매 n건`. */
export function tierStepAt(at) {
  return at > 0 ? `판매 ${at}건` : "시작";
}
