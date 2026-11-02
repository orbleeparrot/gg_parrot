// 화면 파일이 **실제로 묶이는가** — 없는 이름을 들여오면 여기서 걸린다.
//
// 2026-11-02 에 실제로 났다: askCopy.js 에 만든 함수를 askView.js 에서 들여와, 린트도
// 시험 1,066개도 통과했는데 브라우저에서 화면이 통째로 안 떴다("화면을 불러오지 못했어요").
// 라우터·api 를 끌고 와 통째로 그릴 수 없는 파일이라 렌더 시험이 못 잡는 자리다.
import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

// 라우터·훅을 타느라 통째로 그릴 수 없는 큰 화면들. 묶이기만 하면 된다.
const PAGES = [
  "src/components/AskParrotDialog.jsx",
  "src/pages/Studio.jsx",
  "src/components/Builder.jsx",
  "src/components/BundleChips.jsx",
  "src/components/ChartJumpTabs.jsx",
];

for (const page of PAGES) {
  test(`${page} 가 묶인다 — 들여온 이름이 전부 있다`, async () => {
    const result = await build({
      entryPoints: [root + page],
      bundle: true, write: false, format: "esm", platform: "browser", jsx: "automatic",
      loader: { ".js": "jsx", ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
      define: { "process.env.NODE_ENV": '"production"' },
      logLevel: "silent", logLimit: 0,
    });
    assert.deepEqual(result.errors, [], `${page} 묶기 실패`);
  });
}
