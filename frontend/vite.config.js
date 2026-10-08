import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { offlineCache } from "./build/offlineCache.js";

// Dev: proxy /api to the FastAPI backend so the SPA can use relative URLs
// (the same relative URLs also work in production when FastAPI serves dist/).
// 화면 오류 보고(lib/errorReport.js)에 붙이는 빌드 표시 — Vercel 은 커밋 해시, 그 밖에서는 빌드 시각.
const BUILD_ID = (process.env.VERCEL_GIT_COMMIT_SHA || "").slice(0, 7)
  || new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");

export default defineConfig({
  plugins: [react(), tailwindcss(), offlineCache()],
  define: { __BUILD_ID__: JSON.stringify(BUILD_ID) },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.VITE_API_PROXY || "http://localhost:8000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
