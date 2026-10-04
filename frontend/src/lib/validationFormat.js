// 검증 화면의 표시 규칙 — 순수 함수만. 서버가 낸 값을 바꾸거나 다시 계산하지 않는다.

// 못 잰 값(칼마 · 소르티노는 짧거나 평평한 곡선에서 null)은 0 이 아니라 줄표로 보인다.
// 0 으로 보이면 "측정했더니 0" 으로 읽힌다. 숫자가 아닌 값(문자열 포함)도 줄표다.
export function metricText(value, { digits = 2, suffix = "" } = {}) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "—";
  return `${value.toFixed(digits)}${suffix}`;
}

// 구간 막대의 축 길이 = 실패하지 않은 구간 중 절댓값이 가장 큰 수익률. 실패한 구간의 pct 는
// null 이라 Math.abs(null) 이 0 으로 읽히므로, 계산 전에 failed 로 먼저 거른다. 그릴 구간이 없으면 0.
export function barScale(bars) {
  if (!Array.isArray(bars)) return 0;
  let scale = 0;
  for (const bar of bars) {
    if (bar.failed) continue;
    scale = Math.max(scale, Math.abs(bar.pct));
  }
  return scale;
}

const NOT_FOUND = Object.freeze({
  보존_범위_밖: "이 날짜는 보관해 둔 뉴스 범위 밖이라 근거를 찾지 못했어요.",
  조회_실패: "뉴스를 불러오지 못해 근거를 찾지 못했어요. 잠시 뒤 다시 검증해 보세요.",
});

// 서버가 헤드라인을 찾았으면 빈 문자열. 못 찾았으면 '찾지 못했다' 고만 말한다 — 기사가 없었다고
// 단정하지 않는다(우리가 못 찾은 것일 수 있다). reason 을 모르거나 비어 있어도 같은 문장으로 떨어진다.
export function headlineNote(headlines) {
  if (headlines && headlines.found === true) return "";
  const reason = headlines && typeof headlines.reason === "string" ? headlines.reason : "";
  return Object.hasOwn(NOT_FOUND, reason) ? NOT_FOUND[reason] : "이 날짜의 근거를 찾지 못했어요.";
}
