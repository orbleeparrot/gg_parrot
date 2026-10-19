// 시험 도우미가 /tmp 에 묶음 파일을 남기지 않는다(2026-10-19: 3만 8천 개 · 28GB 가 쌓여 서버 디스크가 99% 찼다).
import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { importBundle, lastBundleDir, renderComponent } from "./renderHelper.js";

test("화면을 그린 뒤 임시 폴더를 지운다", async () => {
  const html = await renderComponent("src/components/icons.jsx", { name: "check" }, { exportName: "Icon" });
  assert.match(html, /<svg/);
  assert.ok(lastBundleDir(), "임시 폴더를 썼다");
  assert.equal(existsSync(lastBundleDir()), false, "그리고 지웠다");
});

test("불러오다 실패해도 임시 폴더를 지운다", async () => {
  await assert.rejects(importBundle("throw new Error('깨진 묶음');", "ggp-render-"));
  assert.equal(existsSync(lastBundleDir()), false);
});

test("시험 파일은 임시 폴더를 직접 만들지 않고 도우미를 쓴다", () => {
  // 지우는 코드는 도우미 한 곳에만 있다 — 시험마다 mkdtemp 를 따로 쓰면 다시 새기 쉽다.
  const dir = new URL(".", import.meta.url);
  const offenders = readdirSync(dir)
    .filter((name) => name.endsWith(".js") && !["renderHelper.js", "renderHelperTempCleanup.test.js"].includes(name))
    .filter((name) => /mkdtemp/.test(readFileSync(new URL(name, dir), "utf8")));
  assert.deepEqual(offenders, []);
});
