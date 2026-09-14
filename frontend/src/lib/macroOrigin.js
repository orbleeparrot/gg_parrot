// 실행 세션의 매크로 출처 — 서버가 파일 서명을 검증해 남긴 값(runner.macro_origin).
// 문의가 오면 "웹에서 받은 원본 그대로였는지"를 이 배지 하나로 알 수 있다.
export const MACRO_ORIGIN = {
  web: { label: "웹에서 바로 실행", tone: "flat", hint: "웹 '빠른 실행'으로 서버가 매크로를 직접 넘겼어요." },
  file_verified: { label: "원본 파일", tone: "mine", hint: "껄무새에서 받은 매크로 파일 그대로예요(서명 확인)." },
  file_modified: { label: "수정된 파일", tone: "risk", hint: "웹에서 받은 뒤 로컬에서 고친 파일이에요. 웹 설정과 다르게 동작할 수 있어요." },
  file_unsigned: { label: "서명 없는 파일", tone: "flat", hint: "서명이 없는 옛 파일이라 원본 여부를 확인할 수 없어요." },
};

export function macroOriginBadge(session) {
  const origin = MACRO_ORIGIN[session?.macro_origin];
  if (!origin) return null;
  return {
    ...origin,
    className: "badge badge-" + origin.tone,
    title: origin.hint + (session.macro_digest ? ` · 지문 ${session.macro_digest}` : ""),
  };
}
