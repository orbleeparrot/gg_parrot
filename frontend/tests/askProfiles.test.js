// 성향 재설계(2026-10-12) — 화면 선택지가 서버와 같은가, 경고가 고르는 자리에 있는가.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PROFILES, SHORT_BAR_WARN, WATCH_LEVELS } from "../src/lib/askCopy.js";
import { PROFILE_ORDER } from "../src/lib/askFlow.js";

const source = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

test("성향은 셋이고 순서가 위험 순이다", () => {
  assert.deepEqual(PROFILES.map((p) => p.value), ["stable", "balanced", "aggressive"]);
  assert.deepEqual(PROFILE_ORDER, ["stable", "balanced", "aggressive"],
    "'더 안정적으로 · 더 공격적으로' 가 이 순서를 타고 움직인다");
});

test("성향 힌트가 고정 숫자를 약속하지 않는다", () => {
  // 상한이 기간마다 달라졌다(1주 8% ~ 1년 30%). "-10%까지" 는 이제 거짓이다.
  for (const profile of PROFILES) {
    assert.ok(!/-?\d+\s*%/.test(profile.hint), `${profile.label} 힌트에 숫자가 남았다: ${profile.hint}`);
  }
});

test("확인 빈도에 15분봉 칸이 있다", () => {
  assert.deepEqual(WATCH_LEVELS.map((w) => w.value),
    ["rarely", "sometimes", "often", "constantly"]);
  assert.match(WATCH_LEVELS[3].label, /자주/);
});

test("짧은 봉 경고는 봉을 고르는 자리에 있고 숫자를 댄다", () => {
  assert.match(SHORT_BAR_WARN, /15분/, "어느 봉인지 말해야 한다");
  assert.match(SHORT_BAR_WARN, /64/, "얼마나 어려운지 숫자로 말해야 한다");
  assert.match(SHORT_BAR_WARN, /수수료|슬리피지/, "왜 어려운지 말해야 한다");
  const dialog = source("../src/components/AskParrotDialog.jsx");
  assert.match(dialog, /state\.step === "watch" \?[\s\S]{0,200}SHORT_BAR_WARN/,
    "성향 단계가 아니라 빈도 단계에서 보여야 한다 — 짧은 봉은 빈도가 정한다");
});

test("단타형 흔적이 화면에 남지 않았다", () => {
  for (const path of ["../src/lib/askCopy.js", "../src/lib/askFlow.js",
                      "../src/components/AskParrotDialog.jsx"]) {
    const text = source(path);
    assert.ok(!/scalper/i.test(text), `${path} 에 scalper 가 남았다`);
    assert.ok(!text.includes("단타형"), `${path} 에 단타형이 남았다`);
  }
});
