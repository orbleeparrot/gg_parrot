// 껄무새에게 물어볼까? — 카드 문구·선택지·고지 문구를 한 곳에. '추천' 이라는 말은 쓰지 않는다.
export const DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요";

export const CONSENT_TEXT = "과거 데이터로 조합을 찾아 주는 도구예요. 투자 권유가 아니고, 결과가 미래 수익을 뜻하지 않아요.";

export const STEP_PROMPTS = {
  profile: "손실은 어디까지 견딜 수 있어요?",
  market: "어느 시장에서요?",
  horizon: "얼마나 길게 굴릴 거예요?",
  watch: "하루에 얼마나 자주 볼 수 있어요?",
};

export const PROFILES = [
  { value: "stable", label: "안정형", hint: "-10%까지" },
  { value: "balanced", label: "균형형", hint: "-20%까지" },
  { value: "aggressive", label: "공격형", hint: "제한 없음" },
  { value: "scalper", label: "단타형", hint: "짧은 봉, 빠르게 · 제한 없음" },
];

export const MARKETS = [
  { value: "spot", label: "현물" },
  { value: "futures", label: "선물" },
];
export const LEVERAGES = [1, 2, 3];
export const STABLE_NO_FUTURES = "안정형은 현물만 살펴봐요";

export const HORIZONS = [
  { value: "days", label: "며칠", hint: "짧게 보고 정리" },
  { value: "weeks", label: "몇 주", hint: "적당히" },
  { value: "months", label: "몇 달", hint: "느긋하게" },
  { value: "long", label: "길게", hint: "오래 들고" },
];

export const WATCH_LEVELS = [
  { value: "rarely", label: "거의 못 봐요", hint: "하루 한 번쯤" },
  { value: "sometimes", label: "가끔 봐요", hint: "몇 시간에 한 번" },
  { value: "often", label: "수시로 봐요", hint: "자주 확인" },
];

export const CANDIDATES_PROMPT = "성향에 맞는 종목을 골라 봤어요. 하나를 고르면 매크로 후보를 보여 드릴게요.";
export const MANUAL_PICK_LABEL = "직접 고를래요";

export const FOLLOW_UPS = [
  { kind: "safer", label: "더 안정적으로" },
  { kind: "riskier", label: "더 공격적으로" },
  { kind: "symbols", label: "다른 종목으로" },
  { kind: "restart", label: "처음부터" },
];

export const SCALPER_NOTE = "짧은 봉은 수수료·슬리피지 영향이 커요 · 실행기보다 페이퍼 트레이딩으로 먼저 확인해요";
export const feesNote = (commission, slippage) => `수수료 ${commission}% · 슬리피지 ${slippage}% 포함`;

export const NO_QUOTA_TEXT = "오늘은 다 물어봤어요. 내일 다시 물어봐 주세요.";
export const RUNNING_TEXT = "돌려 볼게요… 후보를 백테스트하는 중이에요";
export const FEW_RESULTS_TEXT = "이 조건에선 후보가 적었어요 — 기간이나 빈도를 바꿔 보세요";
export const NO_RESULTS_TEXT = "이 조건으론 살아남은 후보가 없었어요. 조건을 바꿔 다시 물어봐요.";
export const LOADED_TEXT = "조건 판에 불러왔어요. 숫자 한 번 보고 백테스트부터 돌려 보세요";

// 보여 준 조합이 전부 '그냥 들고 있기' 에 졌을 때 (2026-09-23) — 매크로가 늘 답은 아니라고 먼저 말한다.
export const LOST_TO_HOLD_TEXT = "이번엔 그냥 사서 들고 있는 게 더 나았어요. 아래는 그래도 나은 편이었던 조합이에요.";

// 직접 고를래요 — 거래 가능한 종목을 검색해서 고른다.
export const MANUAL_SEARCH_PLACEHOLDER = "종목 검색 (예: AVAX)";
export const MANUAL_SEARCH_MISS = "목록에 없는 종목이에요";
