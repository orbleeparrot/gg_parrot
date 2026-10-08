import assert from "node:assert/strict";
import test from "node:test";
import { isChunkLoadError, reloadOnceForNewBuild } from "../src/lib/chunkReload.js";

// CI 는 테스트 파일을 한 프로세스에서 돌린다(--test-isolation=none) — 가짜 전역은 테스트가 끝나면 되돌린다.
function fakeBrowser(t, { storageThrows = false } = {}) {
  const store = new Map();
  let reloads = 0;
  const fakes = {
    sessionStorage: {
      getItem: (key) => { if (storageThrows) throw new Error("blocked"); return store.has(key) ? store.get(key) : null; },
      setItem: (key, value) => { if (storageThrows) throw new Error("blocked"); store.set(key, String(value)); },
    },
    window: { location: { reload: () => { reloads += 1; } } },
  };
  for (const [name, value] of Object.entries(fakes)) {
    const previous = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { configurable: true, writable: true, value });
    t.after(() => {
      if (previous) Object.defineProperty(globalThis, name, previous);
      else delete globalThis[name];
    });
  }
  return { reloads: () => reloads };
}

test("배포 뒤 옛 화면 파일을 못 받는 오류만 골라낸다(브라우저마다 문구가 다르다)", () => {
  assert.equal(isChunkLoadError(new TypeError("Failed to fetch dynamically imported module: https://x/assets/Board-1.js")), true);
  assert.equal(isChunkLoadError(new TypeError("Importing a module script failed.")), true);
  assert.equal(isChunkLoadError(new Error("error loading dynamically imported module")), true);
  assert.equal(isChunkLoadError(new Error("Unable to preload CSS for /assets/News.css")), true);
  assert.equal(isChunkLoadError(new TypeError("Cannot read properties of undefined (reading 'exchange')")), false);
  assert.equal(isChunkLoadError(null), false);
});

test("자동 새로고침은 1분에 한 번 — 새 빌드에서도 실패하면 무한히 돌지 않는다", (t) => {
  const browser = fakeBrowser(t);
  assert.equal(reloadOnceForNewBuild(1_000_000), true);
  assert.equal(reloadOnceForNewBuild(1_030_000), false);
  assert.equal(browser.reloads(), 1);
  assert.equal(reloadOnceForNewBuild(1_061_000), true);
  assert.equal(browser.reloads(), 2);
});

test("저장소가 막힌 브라우저에서는 자동 새로고침하지 않는다(기록이 없으면 멈출 수 없다)", (t) => {
  const browser = fakeBrowser(t, { storageThrows: true });
  assert.equal(reloadOnceForNewBuild(1_000_000), false);
  assert.equal(browser.reloads(), 0);
});
