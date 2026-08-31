import assert from "node:assert/strict";
import { readFileSync, statSync } from "node:fs";
import test from "node:test";

const home = readFileSync(new URL("../src/pages/Home.jsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/index.css", import.meta.url), "utf8");

test("designated web fonts swap in instead of staying on system fallbacks", () => {
  const displays = [...css.matchAll(/font-display:\s*([^;]+);/g)].map((match) => match[1].trim());
  assert.ok(displays.length >= 10, "expected all local font faces to declare a display policy");
  assert.deepEqual(new Set(displays), new Set(["swap"]));
});

// 히어로는 래스터 3종(480/800/1180 webp + png 폴백)에서 벡터 한 장으로 바뀌었다.
// 지켜야 할 성질은 그대로다 — 비율을 미리 잡아 레이아웃이 튀지 않고, LCP 이미지라
// 즉시 받으며, 전송량이 예산 안에 있어야 한다.
test("home LCP mascot reserves its ratio and loads eagerly", () => {
  assert.match(css, /\.home-entry-mascot\s*\{[^}]*aspect-ratio:\s*1180\s*\/\s*1120/s);
  assert.match(home, /ggparrot-sunglasses-hero-v2\.svg/);
  assert.match(home, /width="1180"/);
  assert.match(home, /height="1120"/);
  assert.match(home, /loading="eager"/);
  assert.match(home, /fetchPriority="high"/);
});

test("hero vector stays inside its transfer budget", () => {
  const bytes = statSync(new URL("../public/brand/ggparrot-sunglasses-hero-v2.svg", import.meta.url)).size;
  assert.ok(bytes <= 60_000, `hero svg is ${bytes} bytes (budget 60000)`);
});
