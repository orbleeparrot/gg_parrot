// 검증 결과를 화면용 값으로 — 순수 함수만. 서버가 낸 판정을 바꾸거나 다시 계산하지 않는다.
const WARNING_TEXT = Object.freeze({
  한_구간_집중: "수익 대부분이 한 구간에서 나왔어요",
  표본_부족: "거래 수가 적어 통계로 쓰기 어려워요",
  후반부_음수: "뒤쪽 구간에서 성과가 음수예요",
  거래_집중: "소수의 거래가 수익의 절반 이상을 만들었어요",
});

// 모르는 코드는 빈 문자열 — 식별자가 그대로 화면에 나가지 않게 한다.
export function warningText(code) {
  return typeof code === "string" && Object.hasOwn(WARNING_TEXT, code) ? WARNING_TEXT[code] : "";
}

// 구간 하나당 막대 하나. 실행하지 못한 구간(return_pct 가 null 이거나 없음)은
// pct 를 null 로, failed 를 true 로 돌려준다. 0 을 넣지 않는 이유: 화면이 pct 로 막대
// 길이를 잡으면 "본전" 처럼 보이는데, 실제로는 "데이터 없음" 이기 때문이다.
// 화면은 failed 가 true 인 행에서 pct 를 읽지 말고 '데이터 없음' 표시를 그린다.
export function windowBars(windows) {
  if (!Array.isArray(windows)) return [];
  return windows.map((w, i) => {
    const row = w ?? {};
    const pct = typeof row.return_pct === "number" && Number.isFinite(row.return_pct) ? row.return_pct : null;
    return { index: row.index ?? i + 1, pct, failed: pct === null };
  });
}

// 폴백을 'AI 분석' 이라 부르지 않는다 — 일일 챌린지에서 템플릿이 AI 로 둔갑한 적이 있다.
// 정확히 "ai" 만 AI 로 인정하고, 그 밖의 모든 값은 정직한 쪽(자동 요약)으로 떨어진다.
export function analysisLabel(source) {
  return source === "ai" ? "AI 분석" : "자동 요약";
}
