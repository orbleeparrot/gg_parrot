// 컴포넌트를 실제로 그려서 나온 글을 시험하기 위한 도우미. 이미 설치된 esbuild(vite 가 쓰는 것)로 한 파일로 묶고
// react-dom/server 로 그린다 — 새 의존성은 없다. 소스에 글자가 있느냐가 아니라 화면에 나오는 글을 본다.
import { build } from "esbuild";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, sep } from "node:path";
import { pathToFileURL, fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));

// routerState: 라우터 state 로 여는 화면(useLocation().state 를 읽는 쪽)을 그릴 때 쓴다 — 넘기지 않으면 전과 같다.
export async function renderComponent(componentPath, props, { router = false, exportName = "default", routerState = null } = {}) {
  const entry = `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import { MemoryRouter } from "react-router-dom";
    import { ${exportName} as Component } from ${JSON.stringify(join(root, componentPath).split(sep).join("/"))};
    export const render = (props, router, routerState) => {
      const element = React.createElement(Component, props);
      const entries = routerState ? [{ pathname: "/", state: routerState }] : undefined;
      return renderToStaticMarkup(router ? React.createElement(MemoryRouter, entries ? { initialEntries: entries } : null, element) : element);
    };
  `;
  const result = await build({
    stdin: { contents: entry, resolveDir: root, loader: "jsx" },
    jsx: "automatic", bundle: true, write: false, format: "esm", platform: "node",
    loader: { ".js": "jsx", ".css": "empty", ".svg": "dataurl", ".png": "dataurl" },
    define: { "process.env.NODE_ENV": '"production"' },
    banner: { js: 'import { createRequire } from "node:module"; const require = createRequire(import.meta.url);' },
    logLevel: "silent",
  });
  const mod = await importBundle(result.outputFiles[0].text, "ggp-render-");
  return mod.render(props, router, routerState).replace(/<!-- -->/g, "");
}

// 묶은 파일을 잠깐 임시 폴더에 써서 불러오고 바로 지운다 — 불러온 모듈은 메모리에 남는다.
// 2026-10-19: 지우지 않아 그릴 때마다 750KB 폴더가 남았다(/tmp 에 3만 8천 개 · 28GB, 디스크 99%).
let lastDir = "";
export const lastBundleDir = () => lastDir;
export async function importBundle(code, prefix = "ggp-render-") {
  const dir = mkdtempSync(join(tmpdir(), prefix));
  lastDir = dir;
  try {
    const file = join(dir, "bundle.mjs");
    writeFileSync(file, code);
    return await import(pathToFileURL(file).href);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

export const textOf = (html) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
