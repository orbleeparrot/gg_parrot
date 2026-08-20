// 결과 확인 화면의 개인 맞춤 배치.
//
// 사람마다 먼저 보는 것이 다르다. 누구는 차트, 누구는 MDD 부터 본다. 그래서
// 결과 확인 열의 **모든** 단위를 드래그로 위아래 바꿀 수 있게 한다. 큰 블록
// (실시간 차트·수익률 요약·자산곡선…)과 지표 행(승률·MDD·샤프…)이 각각 자기
// 순서를 따로 기억한다.
//
// 저장은 이 브라우저에만. 계정에 붙이지 않는 이유는 "지금 보는 화면의 배치"라서
// 로그인 전에도 동작해야 하기 때문이다. 기기 간 동기화가 필요해지면 User 에
// JSON 컬럼 하나를 두고 여기 load/save 만 서버로 갈아끼우면 된다.

const ORDER_KEY = "ggp_result_block_order";
const STAT_KEY = "ggp_result_stat_order";
// 핀 방식으로 먼저 만들었다가 전부 드래그로 바꿨다. 남은 키는 초기화 때 같이 지운다.
const LEGACY_PIN_KEY = "ggp_result_pinned_stats";

// ResultView 가 늘 가지고 있는 블록. 실시간 차트처럼 바깥(Studio)에서 넣어주는
// 블록은 extraBlocks 로 합쳐지므로 여기 없다.
export const RESULT_BLOCKS = [
  { id: "returns", label: "수익률 요약" },
  { id: "stats", label: "지표 목록" },
  { id: "symbols", label: "종목별 성과" },
  { id: "ai", label: "AI 해설" },
  { id: "equity", label: "자산곡선" },
];

// 지표 행. 순서는 기본 표시 순서이기도 하다.
export const STAT_IDS = [
  "win_rate",
  "mdd",
  "trades",
  "final_equity",
  "sharpe",
  "profit_factor",
  "loss_streak",
];

// 사파리 프라이빗 모드 등에서는 localStorage 접근 자체가 던진다. 배치는 부가
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

// 저장된 순서를 현재 아는 목록에 맞춘다. 없어진 항목은 버리고, 나중에 추가된
// 항목은 기본 위치에 끼워 넣는다 — 새 항목이 늘 맨 끝으로 밀리면 배치를 한 번
// 저장한 사용자만 새 블록을 못 보게 된다.
export function mergeOrder(stored, known) {
  if (!stored || !stored.length) return [...known];
  const merged = [];
  stored.forEach((id) => {
    if (known.includes(id) && !merged.includes(id)) merged.push(id);
  });
  known.forEach((id, defaultIndex) => {
    if (merged.includes(id)) return;
    merged.splice(Math.min(defaultIndex, merged.length), 0, id);
  });
  return merged;
}

export function loadBlockOrder(known) {
  return mergeOrder(readList(ORDER_KEY), known);
}

export function saveBlockOrder(order) {
  writeList(ORDER_KEY, order);
}

export function loadStatOrder() {
  return mergeOrder(readList(STAT_KEY), STAT_IDS);
}

export function saveStatOrder(order) {
  writeList(STAT_KEY, order);
}

export function clearResultLayout() {
  try {
    [ORDER_KEY, STAT_KEY, LEGACY_PIN_KEY].forEach((k) => window.localStorage.removeItem(k));
  } catch (_) {
    // 무시 — 호출부가 상태를 기본값으로 되돌린다.
  }
}

// `id` 를 보이는 이웃 너머로 한 칸 옮긴 전체 순서를 만든다. 숨겨진 항목을 건너뛰지
// 않으면 ↑ 를 눌러도 아무 일이 없는 것처럼 보인다.
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
