// 포인트로 횟수 추가 (2026-09-22) — 서버 status 로부터 "1번 더 물어보기" 버튼의 상태를 정한다.
// 무료가 남아 있으면 안 보여 주고(실수 결제 방지), 포인트 부족·하루 상한이면 버튼만 잠근다.
export function extraOffer(status) {
  if (!status || status.error || !(status.remaining_today <= 0)) return { show: false, canBuy: false, label: "", note: "" };
  const price = Number(status.extra_price) || 0;
  const left = Number(status.extra_left_today) || 0;
  const balance = Number(status.points_balance) || 0;
  const label = `${price}P로 1번 더 물어보기`;
  if (left <= 0) return { show: true, canBuy: false, label, note: "오늘 살 수 있는 추가 횟수를 다 썼어요. 내일 다시 물어봐 주세요." };
  if (balance < price) return { show: true, canBuy: false, label, note: `포인트가 부족해요 (보유 ${balance}P · 필요 ${price}P)` };
  return { show: true, canBuy: true, label, note: `보유 ${balance}P · 오늘 ${left}번 더 살 수 있어요` };
}
