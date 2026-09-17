// 화면 진입 비콘 — 관리자 대시보드의 유입 지표(방문·순방문·유입 출처·UTM). 개인정보는 보내지 않는다:
// 브라우저에 남는 무작위 id 하나(서버는 해시만 저장), 경로, 유입 출처 URL, utm_source. 같은 경로는 30분에 한 번만.
import { getToken } from "./auth.js";

const VISITOR_KEY = "ggp:visitor";
const DEDUPE_MS = 30 * 60 * 1000;
const sent = new Map();

function visitorId() {
  try {
    let id = localStorage.getItem(VISITOR_KEY);
    if (!id) {
      id = (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/-/g, "");
      localStorage.setItem(VISITOR_KEY, id);
    }
    return id;
  } catch {
    return "";
  }
}

export function visitPayload(pathname, { referrer = "", search = "", visitor = "" } = {}) {
  const utm = new URLSearchParams(search).get("utm_source") || "";
  return { path: String(pathname || "/").split("?")[0].slice(0, 120), referrer: String(referrer || "").slice(0, 300), utm_source: utm.slice(0, 60), visitor };
}

export function shouldRecord(path, now = Date.now(), store = sent) {
  if (store.has(path) && now - store.get(path) < DEDUPE_MS) return false;
  store.set(path, now);
  return true;
}

export function recordVisit(pathname) {
  if (typeof window === "undefined" || typeof fetch !== "function") return;
  if (import.meta.env?.DEV && !import.meta.env?.VITE_API_PROXY) return;
  if (!shouldRecord(pathname)) return;
  const payload = visitPayload(pathname, { referrer: document.referrer, search: window.location.search, visitor: visitorId() });
  const token = getToken();
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) };
  fetch("/api/visit", { method: "POST", headers, body: JSON.stringify(payload), keepalive: true }).catch(() => {});
}
