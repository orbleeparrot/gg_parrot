import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App.jsx";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);

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
