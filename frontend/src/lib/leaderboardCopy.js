// 리더보드 행 상태 문구 — 한곳.
export const WAITING = "진입 대기";
export const RECOVERING = "복구 중…";  // 실행 중인데 체크포인트가 없거나 오래됨 — 재배포 직후 잠깐
export const HOLDING = "보유 중";
export const ENTRY_AT = (p) => `진입가 ${p}`;
export const REENTRY = "재진입 대기";
export const COOLDOWN = (m) => `쿨다운 ${m}분`;
export const HALTED = "일일 손실 한도 · 내일 재개";
export const STOPPED = (r) => `종료됨 · 최종 ${r}`;
export const TRADES = (n) => `거래 ${n}회`;
export const KIND = { tp: "익절", sl: "손절", exit: "청산" };
export const LIVE_TITLE = "현재가로 계산한 미실현 수익률 — 3초마다 갱신";

// 수익률 머리글 ⓘ 와 각 행 캡션의 설명 — 상태가 무엇을 뜻하는지.
export const STATE_HELP = {
  waiting: "진입 대기: 매크로가 진입 조건을 기다리는 중이에요. 포지션이 없어 수익률이 변하지 않아요.",
  holding: "보유 중: 포지션을 들고 있어요. 수익률은 현재가로 3초마다 다시 계산돼요(미실현).",
  exited: "청산: 익절·손절로 포지션을 정리했어요. 쿨다운이 끝나면 조건에 맞을 때 다시 진입해요.",
  halted: "일일 손실 한도: 오늘 정한 손실 한도에 닿아 오늘은 더 매매하지 않아요. 내일 재개해요.",
  stopped: "종료됨: 이 페이퍼 세션이 끝났어요. 표시된 수익률이 최종값이에요.",
  recovering: "복구 중: 서버 재시작 직후예요. 잠시 뒤 상태가 채워져요.",
};
export const STATE_LEGEND = [
  ["진입 대기", "조건을 기다리는 중 — 포지션이 없어 수익률이 안 변해요"],
  ["보유 중", "포지션 보유 — 현재가로 3초마다 미실현 수익률 갱신"],
  ["청산", "익절·손절로 정리 — 쿨다운 뒤 재진입 대기"],
  ["일일 손실 한도", "오늘 한도 도달 — 내일 재개"],
  ["종료됨", "세션 종료 — 최종 수익률"],
];
