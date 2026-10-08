// 화면 오류 한 줄을 서버로 — 오류 문장·익명 화면 경로·빌드만(2026-10-07 결정 8, 서버 client_errors.py).
// 같은 오류는 이 탭에서 한 번만 보내고, 한 탭에서 20가지까지만. 보고가 실패해도 화면에는 아무 일도 일어나지 않는다.

const ENDPOINT = "/api/observability/errors";
const MAX_PER_TAB = 20;
// 우리 코드가 아닌 소음 — 브라우저 확장·광고 차단·연결 끊김은 다른 곳(API 오류 안내)에서 다룬다.
const IGNORED = /ResizeObserver loop|Script error\.?$|Failed to fetch$|NetworkError|Load failed|AbortError|The (operation|user) aborted|chrome-extension:|moz-extension:/i;
const sent = new Set();

export const BUILD_ID = typeof __BUILD_ID__ !== "undefined" ? __BUILD_ID__ : "dev";

// 경로에서 쿼리·조각을 떼고 글 번호·공유 주소 같은 값은 :id 로(서버가 한 번 더 화면 틀로 묶는다).
// rum.js 의 같은 함수를 쓰면 늦게 받으려고 떼어 둔 RUM 묶음이 첫 화면으로 딸려 와서 따로 둔다.
export function routeTemplate(rawPath) {
  const path = String(rawPath || "/").split(/[?#]/, 1)[0] || "/";
  const segments = path.split("/").filter(Boolean)
    .map((segment, index, all) => (/\d/.test(segment) || segment.length > 24 || (index === 1 && all[0] === "s") ? ":id" : segment));
  return `/${segments.join("/")}`.slice(0, 200);
}

export function errorMessage(error) {
  if (!error) return "";
  if (typeof error === "string") return error;
  const name = error.name && error.name !== "Error" ? `${error.name}: ` : "";
  return `${name}${error.message || String(error)}`;
}

export function shouldReport(message) {
  return Boolean(message) && !IGNORED.test(message);
}

export function reportClientError(kind, error, { location = globalThis.location, navigatorObject = globalThis.navigator, fetchFn = globalThis.fetch } = {}) {
  try {
    const message = errorMessage(error).slice(0, 1000);
    if (!shouldReport(message)) return false;
    const route = routeTemplate(location?.pathname || "/");
    const key = `${kind}|${route}|${message}`;
    if (sent.has(key) || sent.size >= MAX_PER_TAB) return false;
    sent.add(key);
    const body = JSON.stringify({ kind, route, message, build: BUILD_ID });
    if (typeof navigatorObject?.sendBeacon === "function" && navigatorObject.sendBeacon(ENDPOINT, new Blob([body], { type: "application/json" }))) return true;
    if (typeof fetchFn === "function") {
      fetchFn(ENDPOINT, { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true, credentials: "omit" }).catch(() => {});
      return true;
    }
  } catch {
    /* 오류 보고가 화면을 깨뜨리지 않게 */
  }
  return false;
}

// 테스트용 — 탭 안 중복 기록을 비운다.
export function resetReportedErrors() {
  sent.clear();
}
