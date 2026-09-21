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
