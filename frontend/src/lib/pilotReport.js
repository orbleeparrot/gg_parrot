// 관리자 대시보드 '파일럿 성과' 탭의 글자 만들기 — 서버(/api/admin/pilot)가 계산한 숫자를 그대로 쓰고, 여기서는 모양만 정한다.

export const PILOT_EXCHANGES = { upbit: "업비트", bithumb: "빗썸", binance: "바이낸스" };
export const PILOT_RULES = {
  A: "익절/손절 재진입", B: "지정가 밴드", C: "적립식", D: "그리드", E: "트레일링 스탑", F: "RSI",
  G: "볼린저 밴드", H: "마틴게일", I: "변동성 돌파", J: "이평선 교차", K: "SAR 방어",
};

const MINUS = "−";

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// 표 안 금액 — '원' 없이(단위는 머리글에 한 번), 부호를 늘 붙인다. 반올림해서 0 이면 부호 없이 0.
export function pilotAmount(value) {
  const n = finite(value);
  if (n == null) return "—";
  const r = Math.round(n);
  if (r === 0) return "0";
  return `${r > 0 ? "+" : MINUS}${Math.abs(r).toLocaleString("ko-KR")}`;
}

// 문장 · 제목 속 금액 — 원까지.
export function pilotWon(value, { sign = true } = {}) {
  const n = finite(value);
  if (n == null) return "—";
  const r = Math.round(n);
  if (r === 0) return "0원";
  const head = sign ? (r > 0 ? "+" : MINUS) : r < 0 ? MINUS : "";
  return `${head}${Math.abs(r).toLocaleString("ko-KR")}원`;
}

export function pilotTone(value) {
  const n = finite(value) ?? 0;
  if (n > 0.5) return "is-up";
  if (n < -0.5) return "is-down";
  return "is-zero";
}

export function pilotPct(value) {
  const n = finite(value);
  if (n == null) return "—";
  return `${n > 0 ? "+" : n < 0 ? MINUS : ""}${Math.abs(n).toFixed(2)}%`;
}

export function pilotQty(value) {
  const n = finite(value);
  if (n == null) return "—";
  return n.toLocaleString("ko-KR", { maximumFractionDigits: 4 });
}

function kst(input) {
  const date = new Date(String(input || ""));
  if (Number.isNaN(date.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul", hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).formatToParts(date);
  const pick = (type) => parts.find((p) => p.type === type)?.value || "00";
  return { y: pick("year"), mo: pick("month"), d: pick("day"), h: pick("hour"), mi: pick("minute") };
}

// "10.15 07:51 – 15:55" — 같은 날 끝나면 끝 시각만, 실행 중이면 '지금'.
export function pilotSpan(startIso, endIso, running) {
  const a = kst(startIso);
  if (!a) return "—";
  const head = `${a.mo}.${a.d} ${a.h}:${a.mi}`;
  if (running) return `${head} – 지금`;
  const b = kst(endIso);
  if (!b) return head;
  return b.mo === a.mo && b.d === a.d ? `${head} – ${b.h}:${b.mi}` : `${head} – ${b.mo}.${b.d} ${b.h}:${b.mi}`;
}

export function pilotDuration(startIso, endIso) {
  const ms = new Date(String(endIso || "")).getTime() - new Date(String(startIso || "")).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const hours = ms / 3_600_000;
  if (hours < 1) return `${Math.round(ms / 60_000)}분`;
  if (hours < 48) return `${hours.toFixed(1)}시간`;
  return `${(hours / 24).toFixed(1)}일`;
}

// "2026.10.14 08:00 – 10.16 17:00 KST"
export function pilotPeriod(startIso, endIso) {
  const a = kst(startIso);
  const b = kst(endIso);
  if (!a || !b) return "—";
  return `${a.y}.${a.mo}.${a.d} ${a.h}:${a.mi} – ${b.mo}.${b.d} ${b.h}:${b.mi} KST`;
}

// 손실 합 가운데 가장 큰 손실 계정이 차지하는 몫 — 맨 위 한 줄 요약용. 손실 계정이 없으면 null.
export function pilotWorstShare(accounts) {
  const losers = (accounts || []).filter((a) => a.traded_sessions > 0 && Number(a.net) < -0.5);
  if (!losers.length) return null;
  const total = losers.reduce((sum, a) => sum + Number(a.net), 0);
  const worst = losers.reduce((w, a) => (Number(a.net) < Number(w.net) ? a : w));
  return { name: worst.name, net: Number(worst.net), share: Math.round((Number(worst.net) / total) * 100) };
}

// 세션 목록에서 뺀 것들을 한 문장으로.
export function pilotLeftOut(account) {
  const parts = [];
  if (account.idle_sessions) parts.push(`체결 없이 끝난 실전 세션 ${account.idle_sessions}개`);
  if (account.mock_sessions) parts.push(`모의 세션 ${account.mock_sessions}개`);
  return parts.length ? `목록에서 뺀 것: ${parts.join(", ")}.` : "";
}
