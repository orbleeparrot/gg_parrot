// 프로 빌더 잠금 — 지금 버전에서 멈춰 두고 접근을 막는다(lib/proBuilder.js).
//
// 잠금은 길(App.jsx 라우트)과 메뉴에만 걸려 있고 StudioPro.jsx 는 손대지 않았다. 그래서 이 시험은
// "StudioPro 가 성하다" 와 "그래도 못 들어간다" 를 같이 본다 — 둘 중 하나만 보면 되돌리기가 깨진다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";
import { PRO_BUILDER_LOCK_HINT, PRO_BUILDER_OPEN } from "../src/lib/proBuilder.js";
import { CURRENT_NOTE } from "../src/lib/devNotes.js";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

test("잠금이 실제로 걸려 있다", () => {
  assert.equal(PRO_BUILDER_OPEN, false, "다시 열 때 이 시험을 함께 고쳐라 — 그게 알림장이다");
});

test("/builder/pro 는 StudioPro 가 아니라 닫힘 화면으로 간다", () => {
  const src = read("src/App.jsx");
  const route = /<Route path="\/builder\/pro" element=\{([^}]*)\} \/>/.exec(src);
  assert.ok(route, "/builder/pro 라우트를 찾지 못했다");
  // 깃발을 보고 갈라야 한다. StudioPro 를 무조건 그리면 잠금이 없는 것이고,
  // 라우트를 아예 지우면 되돌릴 때 길이 사라진 것을 모른다.
  assert.match(route[1], /PRO_BUILDER_OPEN/, "라우트가 깃발을 보지 않는다");
  assert.match(route[1], /ProBuilderClosed/, "닫혔을 때 보여 줄 화면이 없다");
  assert.match(src, /import\("\.\/pages\/StudioPro\.jsx"\)/, "StudioPro 는 그대로 남아 있어야 한다");
});

// 드롭다운은 눌러야 열리고 프런트 시험은 SSR 한 번이라, 펼친 모습은 그려지지 않는다.
// 그래서 메뉴는 소스로 본다 — 막는 길이 **세 군데** 다 있어야 한다: 버튼 비활성, 누름 차단, 이유 표시.
// 하나만 보면 나머지 둘을 지워도 초록이다(셋 다 실제로 지워 보고 확인했다).
test("빌더 메뉴의 프로 항목은 남아 있지만 잠겨 있다", () => {
  const menu = read("src/components/BuilderModeMenu.jsx");
  assert.match(menu, /value: "pro".*locked: !PRO_BUILDER_OPEN/,
    "항목을 지우면 '없어졌다' 가 된다 — 남겨 두고 깃발로 잠근다");
  assert.match(menu, /disabled=\{locked\}/, "버튼이 비활성이 아니다");
  assert.match(menu, /onClick=\{\(\) => \{ if \(locked\) return;/, "눌렀을 때 막지 않는다");
  assert.match(menu, /locked \? PRO_BUILDER_LOCK_HINT : hint/, "왜 못 누르는지 말하지 않는다");
});

test("잠긴 항목이 그려질 때 메뉴가 닫혀 있어도 현재 빌더는 멀쩡하다", async () => {
  const html = await renderComponent("src/components/BuilderModeMenu.jsx", { mode: "basic", onSwitch: () => {} });
  assert.match(textOf(html), /기본 빌더/, "지금 빌더 이름이 제목이다");
  assert.doesNotMatch(html, new RegExp(PRO_BUILDER_LOCK_HINT), "닫힌 메뉴에 잠금 문구가 새어 나오면 안 된다");
});

test("닫힘 화면이 왜 닫혔는지와 어디로 가면 되는지 말한다", async () => {
  const html = await renderComponent("src/pages/ProBuilderClosed.jsx", {}, { router: true });
  const text = textOf(html);
  assert.match(text, /업데이트/, "업데이트 예정임을 적어 둔다");
  assert.match(text, /기본 빌더/, "갈 곳을 알려 준다");
  assert.match(text, /남아 있어요|사라지지 않아요/, "저장한 매크로가 안전함을 말한다");
  assert.match(html, /href="\/builder"/, "기본 빌더로 가는 길이 실제 링크여야 한다");
});

test("업데이트 노트가 잠긴 화면으로 보내지 않는다", () => {
  const links = CURRENT_NOTE.items.map((item) => item.link).filter(Boolean);
  assert.ok(links.length > 0, "노트에 길이 하나도 없으면 이 시험이 공허하다");
  for (const link of links) {
    assert.notEqual(link, "/builder/pro", "닫아 둔 화면을 노트가 광고하면 안 된다");
  }
});
