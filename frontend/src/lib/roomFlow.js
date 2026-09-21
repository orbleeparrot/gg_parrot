// 전략방 — 순수 함수만. UI 는 이 결과를 그린다.
const DAY = 24 * 3600 * 1000;
const HOUR = 3600 * 1000;
export const CREATE_COST = 100;
export const MIN_ACCOUNT_AGE_MS = 3 * DAY;

export function remainingLabel(expiresMs, nowMs = Date.now()) {
  const left = Number(expiresMs) - nowMs;
  if (left <= 0) return "끝남";
  if (left >= DAY) return `${Math.floor(left / DAY)}일 남음`;
  if (left >= HOUR) return `${Math.floor(left / HOUR)}시간 남음`;
  return "곧 종료";
}

function accountAgeMs(me, nowMs) {
  const created = Date.parse(me?.created_at || "");
  return Number.isFinite(created) ? nowMs - created : Infinity;
}

export function canJoin(room, me, nowMs = Date.now()) {
  if (!me) return { ok: false, reason: "로그인이 필요해요" };
  if (room.is_member) return { ok: false, reason: "이미 들어와 있어요" };
  if (!room.is_open || Number(room.expires_ms) <= nowMs) return { ok: false, reason: "끝난 방이에요" };
  if (room.member_count >= room.capacity) return { ok: false, reason: "정원이 다 찼어요" };
  if (room.entry_fee > 0) {
    if (accountAgeMs(me, nowMs) < MIN_ACCOUNT_AGE_MS) return { ok: false, reason: "가입 3일 후부터 유료방에 들어갈 수 있어요" };
    if ((me.points_balance ?? 0) < room.entry_fee) return { ok: false, reason: "포인트가 부족해요" };
  }
  return { ok: true, reason: "" };
}

export function joinConfirmText(room) {
  if (!room.entry_fee) return `'${room.title}' 방에 들어갈까요?`;
  return `'${room.title}' 방에 들어가면 ${room.entry_fee}포인트가 차감돼요. 나가도 환불은 없어요.`;
}

export function validateCreate({ title, capacity, entryFee }) {
  const errors = {};
  const t = String(title || "").trim();
  if (t.length < 2 || t.length > 30) errors.title = "2~30자로 적어 주세요";
  const cap = Number(capacity);
  if (!Number.isInteger(cap) || cap < 2 || cap > 10) errors.capacity = "2~10명이에요";
  const fee = Number(entryFee);
  if (!Number.isInteger(fee) || fee < 0 || fee > 300) errors.entryFee = "0~300포인트예요";
  return { ok: Object.keys(errors).length === 0, errors };
}

// 탭: 전체 · 활성 방 · 열린 내 방 · 끝난 내 방 · 방 찾기
export function roomTabs(mine, activeRoomId) {
  const rooms = [...(mine || [])].sort((a, b) => {
    if (a.id === activeRoomId) return -1;
    if (b.id === activeRoomId) return 1;
    if (a.is_open !== b.is_open) return a.is_open ? -1 : 1;
    return 0;
  });
  return [
    { key: "all", label: "전체" },
    ...rooms.map((room) => ({ key: `room:${room.id}`, roomId: room.id, label: room.title, ended: !room.is_open })),
    { key: "find", label: "방 찾기" },
  ];
}
