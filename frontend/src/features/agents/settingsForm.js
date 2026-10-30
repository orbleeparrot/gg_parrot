// 구동 중 매크로 설정 변경 — 화면이 입력한 값을 서버가 받는 모양으로 바꾼다 (2026-10-30).
//
// **무엇을 바꿀 수 있는지는 여기서 정하지 않는다.** 서버(app/runner_settings.py)가 칸 목록을
// 내려 주고(session.settings.fields) 여기서는 그 칸만 그린다. 목록을 여기 또 두면 두 벌이
// 되어 어긋난다 — 화면에는 보이는데 서버가 거절하는 칸이 생기고, 사용자는 고장으로 읽는다.

const KEEP = Symbol("변경 없음");

// "1,000,000" · " 3.5 " 처럼 사람이 친 숫자를 받는다. 숫자가 아니면 null.
export function parseNumber(text) {
  const cleaned = String(text ?? "").replace(/,/g, "").trim();
  if (!cleaned) return null;
  const n = Number(cleaned);
  return Number.isFinite(n) ? n : null;
}

// 칸 하나의 현재 값을 입력란에 넣을 글자로. 없는 값(손절 꺼 둠)은 빈 칸이다.
export function fieldText(field) {
  const v = field?.value;
  if (v == null) return "";
  return field.unit === "money" ? Number(v).toLocaleString() : String(v);
}

// 단위를 붙여 읽는 말로. 확인 창과 바뀐 내용 요약이 함께 쓴다.
export function showValue(field, value) {
  if (value == null || value === "") return "없음";
  const n = typeof value === "number" ? value : parseNumber(value);
  if (n == null) return String(value);
  switch (field?.unit) {
    case "money": return n.toLocaleString();
    case "pct": return `${n}%`;
    case "ratio": return `${Math.round(n * 100)}%`;
    case "hours": return `${n}시간`;
    case "minutes": return `${n}분`;
    case "days": return `${n}일`;
    default: return String(n);
  }
}

// 입력란의 글자 하나를 '보낼 값'으로. KEEP 이면 안 보낸다(= 그대로 둔다).
function intended(field, text) {
  const raw = String(text ?? "");
  if (raw.trim() === "") {
    // 빈 칸의 뜻은 칸마다 다르다. 끌 수 있는 칸(손절 등)을 비우면 **끄는 것**이고,
    // 원래 비어 있었으면 바뀐 게 없다. 끌 수 없는 칸을 비운 건 입력을 덜 한 것이다.
    if (!field.clearable) return KEEP;
    return field.value == null ? KEEP : null;
  }
  const n = parseNumber(raw);
  if (n == null) return KEEP;
  return n === Number(field.value) ? KEEP : n;
}

// 칸 목록 + 입력값 → 서버에 보낼 changes. 바뀐 칸만 담는다(부분 수정).
export function buildChanges(fields = [], edits = {}) {
  const changes = {};
  for (const field of fields) {
    if (!Object.prototype.hasOwnProperty.call(edits, field.path)) continue;
    const value = intended(field, edits[field.path]);
    if (value === KEEP) continue;
    (changes[field.section] ||= {})[field.key] = value;
  }
  return changes;
}

// 사람이 친 글자 중 숫자로 읽히지 않는 것들 — 보내기 전에 화면이 먼저 말해 준다.
// **손댄 칸만 본다.** 안 건드린 칸까지 보면 열어 두기만 해도 전부 빨개진다.
export function badFields(fields = [], edits = {}) {
  return fields.filter((field) => {
    if (!Object.prototype.hasOwnProperty.call(edits, field.path)) return false;
    const raw = String(edits[field.path] ?? "");
    if (raw.trim() === "") return !field.clearable;  // 비울 수 없는 칸을 비웠다
    return parseNumber(raw) == null;                 // 숫자로 안 읽힌다
  });
}

export function hasChanges(fields, edits) {
  const changes = buildChanges(fields, edits);
  return Object.values(changes).some((section) => Object.keys(section).length > 0);
}

// 확인 창이 읽어 주는 한 줄 — "투자 금액 1,000,000 → 2,000,000 · 손절 3% → 없음".
export function changeSummary(fields = [], edits = {}) {
  const changes = buildChanges(fields, edits);
  const parts = [];
  for (const field of fields) {
    const next = changes[field.section]?.[field.key];
    if (next === undefined) continue;
    parts.push(`${field.label} ${showValue(field, field.value)} → ${showValue(field, next)}`);
  }
  return parts.join(" · ");
}
