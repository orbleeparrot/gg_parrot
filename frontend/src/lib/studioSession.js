// 직접 만들기 작업 상태 — 다른 화면(마이페이지 등)에 다녀와도 조건·결과·탭이 그대로 남도록 이 탭(sessionStorage)에 둔다.
// 새로고침해도 남고, 탭을 닫으면 사라진다. 공유 링크(/s/:slug)로 들어온 화면은 저장하지도 읽지도 않는다.
import { getAuthScope } from "./auth.js";
import { accountStorageKey, discardUnownedDrafts } from "./accountStorage.js";

export function studioPaperKey(scope = getAuthScope()) {
  return scope === "pending" ? "" : accountStorageKey("paper", scope);
}

export function readStudioSession(scope = getAuthScope()) {
  try {
    discardUnownedDrafts();
    if (scope !== getAuthScope() || scope === "pending") return null;
    const value = JSON.parse(sessionStorage.getItem(accountStorageKey("studio", scope)) || "null");
    return value && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

export function writeStudioSession(value, scope = getAuthScope()) {
  try {
    if (scope !== getAuthScope() || scope === "pending") return;
    sessionStorage.setItem(accountStorageKey("studio", scope), JSON.stringify(value));
  } catch {
    // 저장소가 막힌 브라우저(사생활 보호 모드 등)면 그냥 넘어간다 — 화면 동작에는 영향이 없다.
  }
}

export function clearStudioSession(scope = getAuthScope()) {
  try {
    sessionStorage.removeItem(accountStorageKey("studio", scope));
  } catch {
    // 위와 같다.
  }
}
