// 껄무새에게 물어볼까? — 카드 문구·선택지·고지 문구를 한 곳에. '추천' 이라는 말은 쓰지 않는다.
export const DISCLAIMER = "AI 가 과거 데이터로 고른 후보예요 · 투자 권유가 아니에요 · 과거 성과는 미래 수익을 보장하지 않아요";

export const CONSENT_TEXT = "과거 데이터로 조합을 찾아 주는 도구예요. 투자 권유가 아니고, 결과가 미래 수익을 뜻하지 않아요.";

export const STEP_PROMPTS = {
  profile: "손실은 어디까지 견딜 수 있어요?",
  market: "어느 시장에서요?",
  symbols: "어떤 종목이 궁금해요? (최대 3개)",
  period: "어느 기간을 살펴볼까요?",
  interval: "얼마나 자주 사고팔고 싶어요?",
};

export const PROFILES = [
  { value: "stable", label: "안정형", hint: "-10%까지" },
  { value: "balanced", label: "균형형", hint: "-20%까지" },
  { value: "aggressive", label: "공격형", hint: "제한 없음" },
];

export const MARKETS = [
  { value: "spot", label: "현물" },
  { value: "futures", label: "선물" },
];
export const LEVERAGES = [1, 2, 3];
export const STABLE_NO_FUTURES = "안정형은 현물만 살펴봐요";

export const POPULAR_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"];

export const PERIODS = [
  { value: "3m", label: "최근 3개월" },
  { value: "6m", label: "최근 6개월" },
  { value: "1y", label: "최근 1년" },
];

export const INTERVALS = [
  { value: "1h", label: "자주", hint: "1시간 봉" },
  { value: "4h", label: "보통", hint: "4시간 봉" },
  { value: "1d", label: "느긋하게", hint: "하루 봉" },
];

export const FOLLOW_UPS = [
  { kind: "safer", label: "더 안정적으로" },
  { kind: "riskier", label: "더 공격적으로" },
  { kind: "symbols", label: "다른 종목으로" },
  { kind: "restart", label: "처음부터" },
];

export const NO_QUOTA_TEXT = "오늘은 다 물어봤어요. 내일 다시 물어봐 주세요.";
export const RUNNING_TEXT = "돌려 볼게요… 후보를 백테스트하는 중이에요";
export const FEW_RESULTS_TEXT = "이 조건에선 후보가 적었어요 — 기간이나 빈도를 바꿔 보세요";
export const NO_RESULTS_TEXT = "이 조건으론 살아남은 후보가 없었어요. 조건을 바꿔 다시 물어봐요.";
export const LOADED_TEXT = "조건 판에 불러왔어요. 숫자 한 번 보고 백테스트부터 돌려 보세요";
