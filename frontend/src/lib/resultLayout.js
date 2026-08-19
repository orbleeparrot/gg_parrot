// 결과 확인 화면의 개인 맞춤 배치 — 블록 순서와 지표 고정(핀).
//
// 사람마다 먼저 보는 수치가 다르다. 누구는 MDD, 누구는 샤프지수부터 본다.
// 큰 블록은 순서를 바꾸고(드래그 또는 ↑↓), 지표 행은 핀으로 맨 위에 고정한다.
//
// 저장은 이 브라우저에만. 계정에 붙이지 않는 이유는 "지금 보는 화면의 배치"라서
// 로그인 전에도 동작해야 하기 때문이다. 기기 간 동기화가 필요해지면 User 에
// JSON 컬럼 하나를 두고 여기 load/save 만 서버로 갈아끼우면 된다.

const ORDER_KEY = "ggp_result_block_order";
const PIN_KEY = "ggp_result_pinned_stats";

// 순서를 바꿀 수 있는 블록. 청산 경고와 각주는 여기 없다 — 전액 손실 알림을
// 사용자가 맨 아래로 내려버릴 수 있으면 안 된다.
export const RESULT_BLOCKS = [
  { id: "returns", label: "수익률 요약" },
  { id: "stats", label: "지표 목록" },
  { id: "symbols", label: "종목별 성과" },
  { id: "ai", label: "AI 해설" },
  { id: "equity", label: "자산곡선" },
];

export const DEFAULT_BLOCK_ORDER = RESULT_BLOCKS.map((b) => b.id);

// 핀으로 고정할 수 있는 지표 행. 순서는 기본 표시 순서이기도 하다.
export const STAT_IDS = [
  "win_rate",
  "mdd",
  "trades",
  "final_equity",
  "sharpe",
  "profit_factor",
  "loss_streak",
];

// 사파리 프라이빗 모드 등에서 localStorage 접근 자체가 던진다. 배치는 부가
// 기능이라 실패해도 화면은 기본값으로 그대로 동작해야 한다.
function readList(key) {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? JSON.parse(raw) : null;
    return Array.isArray(parsed) ? parsed.filter((v) => typeof v === "string") : null;
  } catch (_) {
    return null;
  }
}

function writeList(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {
    // 저장만 못 할 뿐, 이번 세션 동안의 배치는 그대로 유지된다.
  }
}

// 저장된 순서를 현재 코드가 아는 블록 목록에 맞춘다. 없어진 블록은 버리고,
// 나중에 추가된 블록은 기본 위치에 끼워 넣는다 — 새 블록이 늘 맨 끝으로
// 밀리면 배치를 한 번 저장한 사용자만 새 기능을 못 보게 된다.
export function mergeBlockOrder(stored) {
  if (!stored || !stored.length) return [...DEFAULT_BLOCK_ORDER];
  const kept = stored.filter((id) => DEFAULT_BLOCK_ORDER.includes(id));
  const merged = [];
  kept.forEach((id) => {
    if (!merged.includes(id)) merged.push(id);
  });
  DEFAULT_BLOCK_ORDER.forEach((id, defaultIndex) => {
    if (merged.includes(id)) return;
    merged.splice(Math.min(defaultIndex, merged.length), 0, id);
  });
  return merged;
}

export function loadBlockOrder() {
  return mergeBlockOrder(readList(ORDER_KEY));
}

export function saveBlockOrder(order) {
  writeList(ORDER_KEY, order);
}

export function loadPinnedStats() {
  const stored = readList(PIN_KEY);
  return (stored || []).filter((id) => STAT_IDS.includes(id));
}

export function savePinnedStats(pinned) {
  writeList(PIN_KEY, pinned);
}

export function clearResultLayout() {
  try {
    window.localStorage.removeItem(ORDER_KEY);
    window.localStorage.removeItem(PIN_KEY);
  } catch (_) {
    // 무시 — 호출부가 상태를 기본값으로 되돌린다.
  }
}

// 보이는 블록만 추려 저장된 순서대로 돌려준다. 숨겨진 블록(예: 단일 종목이라
// 종목별 표가 없는 경우)도 저장 순서에는 남아 있어, 다시 나타나면 원래 자리로
// 돌아온다.
export function orderedVisibleBlocks(order, isVisible) {
  return order.filter((id) => isVisible(id));
}

// `id` 를 보이는 이웃 블록 너머로 한 칸 옮긴 전체 순서를 만든다. 숨겨진 블록을
// 건너뛰지 않으면 ↑ 를 눌러도 아무 일이 없는 것처럼 보인다.
export function moveWithin(order, id, dir, visibleIds) {
  const visible = order.filter((x) => visibleIds.includes(x));
  const target = visible[visible.indexOf(id) + dir];
  if (!target) return order;
  return insertRelative(order, id, target, dir > 0);
}

// `id` 를 목록에서 빼내 `targetId` 앞/뒤에 다시 넣는다.
export function insertRelative(order, id, targetId, after) {
  if (id === targetId) return order;
  const from = order.indexOf(id);
  const to = order.indexOf(targetId);
  if (from < 0 || to < 0) return order;
  const next = order.slice();
  next.splice(from, 1);
  next.splice(next.indexOf(targetId) + (after ? 1 : 0), 0, id);
  return next;
}
