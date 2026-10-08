import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { RootErrorBoundary } from "./components/RouteErrorBoundary.jsx";
import { reloadOnceForNewBuild } from "./lib/chunkReload.js";
import { toastHost } from "./lib/toastHost.js";
import { reportClientError } from "./lib/errorReport.js";
import "./index.css";

// 토스트 자리(status 영역)를 먼저 만들어 둔다 — 토스트와 함께 생기면 화면 읽기 프로그램이 첫 토스트를 놓친다.
toastHost();

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <RootErrorBoundary>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </RootErrorBoundary>
  </React.StrictMode>
);

// 배포 직후 옛 화면 파일을 미리 받다 실패하면(Vite preload) 한 번 새로고침해 새 빌드를 받는다.
// preventDefault 는 부르지 않는다 — 부르면 Vite 가 오류 대신 undefined 를 돌려줘 React.lazy 가 '.default' 를 읽다
// "Cannot read properties of undefined (reading 'default')" 로 터지고, 새 버전 파일 오류가 '화면 그리기'로
// 잘못 모였다(2026-10-08 /leaderboard·/admin). 오류를 그대로 올리면 경계가 새 버전 파일 오류로 알아본다.
window.addEventListener("vite:preloadError", () => {
  reloadOnceForNewBuild();
});

// 경계 밖에서 난 오류(이벤트 처리기·비동기)도 화면 오류로 모은다 — 우리 파일에서 난 것만(확장 프로그램·외부 위젯 제외).
window.addEventListener("error", (event) => {
  const file = String(event.filename || "");
  if (file && !file.startsWith(window.location.origin)) return;
  reportClientError("unhandled", event.error || event.message);
});
window.addEventListener("unhandledrejection", (event) => {
  const reason = event.reason;
  // 서버 응답(status)·연결 끊김·취소·시간 초과는 화면이 이미 안내하는 일이라 모으지 않는다.
  if (!(reason instanceof Error) || reason.status || reason.code === "NETWORK" || /^(AbortError|TimeoutError)$/.test(reason.name)) return;
  reportClientError("rejection", reason);
});

// RUM is deliberately split into a late chunk so Core Web Vitals measurement
// never competes with the initial React render or the home LCP image.
const loadRum = () => {
  import("./lib/rum.js")
    .then(({ startRum }) => startRum())
    .catch(() => {});
};
if (typeof window.requestIdleCallback === "function") {
  window.requestIdleCallback(loadRum, { timeout: 2_000 });
} else {
  window.setTimeout(loadRum, 1_000);
}

// Static assets and a generic offline page only. Dev/HMR never installs a worker.
if (import.meta.env.PROD && "serviceWorker" in navigator) {
  const register = () => navigator.serviceWorker.register("/sw.js", { updateViaCache: "none" }).catch(() => {});
  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, { once: true });
}
