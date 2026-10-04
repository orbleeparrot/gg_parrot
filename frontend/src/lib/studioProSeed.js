// 프로 빌더가 시작할 조건 — 기본 빌더의 '프로로 열기' 가 router state.macro 로 지금 조건을 넘긴다.
// history.state 는 새로고침 뒤에도 남으므로, 배포로 매매 방식이 바뀐 옛 항목이나 손댄 값이 와도 화면이
// 비지 않게 한다: 매크로가 조건 판이 그릴 수 있는 값으로 왔다 갔다(macroToForm → buildMacro)
// 되는지 확인하고, 어느 한 단계라도 막히면 기본 조건으로 시작한다.
import { RULE_TYPES, buildMacro, defaultForm, macroToForm } from "./macro.js";

export function seedForm(state) {
  const macro = state?.macro;
  if (!macro || typeof macro !== "object" || Array.isArray(macro)) return defaultForm();
  if (typeof macro.rule_type !== "string" || !Object.hasOwn(RULE_TYPES, macro.rule_type)) return defaultForm();
  try {
    const form = macroToForm(macro);
    buildMacro(form); // 거래소 · 종목처럼 한 바퀴 돌려야 드러나는 오류를 여기서 잡는다
    return form;
  } catch (_) {
    return defaultForm();
  }
}
