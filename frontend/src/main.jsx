import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import { RootErrorBoundary } from "./components/RouteErrorBoundary.jsx";
import { reloadOnceForNewBuild } from "./lib/chunkReload.js";
import { toastHost } from "./lib/toastHost.js";
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
window.addEventListener("vite:preloadError", (event) => {
  if (reloadOnceForNewBuild()) event.preventDefault();
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
