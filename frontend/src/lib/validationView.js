// 검증 결과를 화면용 값으로 — 순수 함수만. 서버가 낸 판정을 바꾸거나 다시 계산하지 않는다.
const WARNING_TEXT = Object.freeze({
  한_구간_집중: "수익 대부분이 한 구간에서 나왔어요",
  표본_부족: "거래 수가 적어 통계로 쓰기 어려워요",
  후반부_음수: "뒤쪽 구간에서 성과가 음수예요",
  거래_집중: "소수의 거래가 수익의 절반 이상을 만들었어요",
});

// 서버가 이 화면이 모르는 경고 코드를 보내면 코드 이름 대신 이 문장을 보인다. 화면에서 경고가
// 말없이 사라지면 "분석은 좋은 말을 하지 않는다" 가 깨진다 — 모르면 모른다고 드러낸다.
export const UNKNOWN_WARNING_TEXT = "서버가 새 경고를 표시했어요";

// 이 화면이 아는 코드 목록. 백엔드 engine/validation.py 의 WARNING_CODES 와 같아야 하며 시험이 맞춘다.
export const KNOWN_WARNING_CODES = Object.freeze(Object.keys(WARNING_TEXT));

// 모르는 코드(문자열이 아닌 값 포함)는 식별자를 그대로 내지 않고 일반 문장으로 바꾼다.
export function warningText(code) {
  return typeof code === "string" && Object.hasOwn(WARNING_TEXT, code) ? WARNING_TEXT[code] : UNKNOWN_WARNING_TEXT;
}

// 조건 판의 값이 같은지 — 값으로 견준다(같은 객체가 아니라 같은 내용). '조건을 바꿨어요' 는 화면에 있는
// 결과를 만든 조건과 지금 조건이 다를 때만 떠야 하는데, 조건 판은 값이 안 변하는 갱신도 setForm 으로
// 보내므로(실제 펀딩비 적용을 두 번 누름, 거래소 전환 뒤 늦게 도착하는 종목 복원) 호출 횟수로 세지 않는다.
export function sameForm(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || a === null || b === null) return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  const keysA = Object.keys(a);
  const keysB = Object.keys(b);
  if (keysA.length !== keysB.length) return false;
  return keysA.every((key) => Object.hasOwn(b, key) && sameForm(a[key], b[key]));
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
