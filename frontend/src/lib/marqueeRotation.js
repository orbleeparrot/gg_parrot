// 하단 '오늘의 경주마' 띠 — 거래소를 차례로 돌린다(업비트 → 빗썸 → 바이낸스 → 업비트 …, 2026-10-17 사용자 요청).
export const MARQUEE_EXCHANGES = ["upbit", "bithumb", "binance"];
// 한 거래소의 TOP10 이 한 바퀴 다 지나간 뒤 이만큼 더 보여 주고 다음 거래소로 넘긴다.
export const MARQUEE_HOLD_MS = 5_000;
// 움직임 줄이기(prefers-reduced-motion)면 띠가 흐르지 않으니 이 시간마다 넘긴다.
export const MARQUEE_STILL_MS = 20_000;

// 다음에 보여 줄 거래소 — 목록이 빈(아직 못 받았거나 실패한) 거래소는 건너뛴다. 보여 줄 게 없으면 "".
export function nextMarqueeExchange(current, lists, order = MARQUEE_EXCHANGES) {
  const start = Math.max(0, order.indexOf(current));
  for (let step = 1; step <= order.length; step += 1) {
    const name = order[(start + step) % order.length];
    if (lists?.[name]?.length) return name;
  }
  return "";
}

// 지금 보여 줄 거래소 — 정해 둔 거래소 목록이 비었으면 그다음 것.
export function shownMarqueeExchange(current, lists, order = MARQUEE_EXCHANGES) {
  return lists?.[current]?.length ? current : nextMarqueeExchange(current, lists, order);
}
