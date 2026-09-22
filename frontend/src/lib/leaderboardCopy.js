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
  ["거래 N회", "등록 이후 체결된 횟수(진입·청산 각각 1회). 익절·손절 뒤 다시 진입하면 계속 늘어요"],
];
export const TRADES_HELP = "거래 N회: 등록 이후 체결 횟수예요. 진입 1회 + 청산 1회 = 2회. 재진입할수록 늘어요.";

// 순위 포인트 보상 (2026-09-22) — 자정 이월 때 어제 최종 순위로 지급(backend/app/leaderboard.py RANK_REWARDS 와 같은 값).
export const REWARD_NOTE = "· 순위권 포인트 보상 ⓘ";
export const REWARD_HELP = "매일 자정 어제 순위로 지급 — 1등 100P · 2등 60P · 3등 40P · 4~10등 15P. 상위 3등을 연속으로 지키면 하루당 +10P(최대 +50P). 1인 1회, AI 봇 제외.";
