// 성향 재설계(2026-10-12) — 화면 선택지가 서버와 같은가, 경고가 고르는 자리에 있는가.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { PROFILES, SHORT_BAR_WARN, TUNE_NOTE, WATCH_LEVELS } from "../src/lib/askCopy.js";
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
  assert.match(dialog, /state\.step === "watch"[\s\S]{0,200}SHORT_BAR_WARN/,
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

test("AI 해설 호출이 화면에서 사라졌다", () => {
  // AI 층만 벗긴 것이다 — 해설은 백테스트 응답에 실려 그대로 보인다.
  for (const path of ["../src/api.js", "../src/pages/Studio.jsx",
                      "../src/hooks/useHeroBacktest.js",
                      "../src/components/ResultView.jsx",
                      "../src/components/StudioDock.jsx",
                      "../src/components/HeroGuideScreens.jsx"]) {
    const text = source(path);
    assert.ok(!/explainAi|explain\/ai/.test(text), `${path} 에 AI 해설 호출이 남았다`);
    // 대문자 변형(setAiBusy)과 요청 번호 ref 까지 본다 — 선언만 지우고 호출이 남으면
    // 화면이 ReferenceError 로 죽는다(실제로 한 번 그랬다).
    assert.ok(!/[aA]iBusy|[aA]iError|onAiExplain|aiRequestIdRef|aiPendingRef/.test(text),
      `${path} 에 AI 전용 상태가 남았다`);
  }
  const view = source("../src/components/ResultView.jsx");
  assert.match(view, /explanation\.headline/, "해설은 그대로 보여 줘야 한다");
  assert.ok(!view.includes("AI 해설"), "라벨에서 'AI' 가 빠져야 한다");
});

test("'길게' 를 고른 사람에게는 15분봉 경고를 보이지 않는다", () => {
  // STEPS 가 horizon → watch 순서라 빈도 카드를 볼 때 기간은 이미 정해져 있다. 그리고
  // 서버는 15분 × 1년을 1시간봉으로 내린다(봉 상한) — 그 사람에게 "15분봉이에요 · 64%가
  // 손실" 은 사실이 아니고, 고르는 순간에 거짓을 말하는 셈이다.
  const dialog = source("../src/components/AskParrotDialog.jsx");
  assert.match(dialog, /answers\.horizon !== "long"/,
    "기간이 '길게' 면 짧은 봉 경고가 뜨지 않아야 한다");
  assert.ok(!/15분/.test(WATCH_LEVELS[3].hint),
    `힌트도 기간에 따라 달라지므로 고정 숫자를 쓰면 안 된다: ${WATCH_LEVELS[3].hint}`);
});

test("조합을 그대로 믿지 말라고 말한다", () => {
  // 같은 조건으로 물어보면 같은 값이 나온다 — _plan·build_templates·select_top 에 무작위가
  // 없다. 그리고 그 값은 고른 구간에 맞춘 값이다. 자동 최적화 화면은 이미 '과거 적합' 을
  // 경고하는데 물어볼까만 같은 성질의 값을 내면서 아무 말도 안 하고 있었다.
  assert.match(TUNE_NOTE, /같은 값|같은 조건/, "값이 나만의 것이 아니라는 걸 말해야 한다");
  assert.match(TUNE_NOTE, /바꿔|조정/, "무엇을 하라는 건지 말해야 한다");
  assert.match(TUNE_NOTE, /아무 일|기다|대기/, "한참 안 사질 수 있다는 걸 말해야 한다");
});

test("안내 문구가 단정하거나 권유하지 않는다", () => {
  // 이 흐름의 문구 규칙 — '추천' 이라 쓰지 않고, '최적' 처럼 단정하지 않는다.
  assert.ok(!TUNE_NOTE.includes("추천"), "'추천' 은 이 흐름에서 쓰지 않는다");
  assert.ok(!/최적|보장|확실/.test(TUNE_NOTE), `단정하는 말이 들어갔다: ${TUNE_NOTE}`);
  assert.ok(TUNE_NOTE.length <= 200, `코린이가 읽기엔 길다(${TUNE_NOTE.length}자)`);
});

test("불러오기 확인 팝업에서 그 안내를 띄운다", () => {
  // 결과 화면 맨 아래는 고지 문구와 붙어 있어 잘 안 읽힌다. 불러오기를 누른 순간이
  // 가장 잘 보이는 자리다 — 조건 판에 넣기 직전이라 '조금씩 바꿔 보세요' 가 바로 다음 행동이다.
  const studio = source("../src/pages/Studio.jsx");
  assert.match(studio, /warning=\{TUNE_NOTE\}/, "확인 팝업의 경고 칸에 실어야 한다");
  // 조건 판이 비어 있다고 팝업을 건너뛰면, 처음 쓰는 사람이 이 안내를 한 번도 못 본다 —
  // 그 사람이 바로 이 안내가 필요한 사람이다.
  assert.ok(!/if \(untouched\) applyAskMacro/.test(studio),
    "비어 있을 때도 확인을 거쳐야 한다");
  const dialog = source("../src/components/AskParrotDialog.jsx");
  assert.ok(!dialog.includes("TUNE_NOTE"),
    "결과 화면에서는 뺀다 — 같은 말을 두 군데서 하지 않는다");
});
