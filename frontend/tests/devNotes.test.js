import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { CURRENT_NOTE, shouldShowDevNote, dismissDevNote } from "../src/lib/devNotes.js";

function store() {
  const m = new Map();
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k) };
}

test("note has plain, short lines", () => {
  assert.ok(CURRENT_NOTE.id && CURRENT_NOTE.title);
  assert.ok(CURRENT_NOTE.items.length >= 3);
  for (const it of CURRENT_NOTE.items) assert.ok(it.text.length <= 60, it.text);
});

test("shows on first visit; closing without the checkbox hides only for this tab session", () => {
  const local = store(); const session = store();
  assert.equal(shouldShowDevNote({ local, session, todayKst: "2026-09-22" }), true);
  dismissDevNote({ local, session, todayKst: "2026-09-22", hideToday: false });
  assert.equal(shouldShowDevNote({ local, session, todayKst: "2026-09-22" }), false);
  assert.equal(shouldShowDevNote({ local, session: store(), todayKst: "2026-09-22" }), true); // 새 탭
});

test("'오늘 하루만 보기' hides until the KST day changes; a new note id shows again", () => {
  const local = store(); const session = store();
  dismissDevNote({ local, session, todayKst: "2026-09-22", hideToday: true });
  assert.equal(shouldShowDevNote({ local, session: store(), todayKst: "2026-09-22" }), false);
  assert.equal(shouldShowDevNote({ local, session: store(), todayKst: "2026-09-23" }), true);
  local.setItem("devnote:hide-today", JSON.stringify({ id: "old-note", day: "2026-09-22" }));
  assert.equal(shouldShowDevNote({ local, session: store(), todayKst: "2026-09-22" }), true);
});

test("storage failures never throw", () => {
  const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
  assert.equal(shouldShowDevNote({ local: broken, session: broken, todayKst: "2026-09-22" }), true);
  assert.doesNotThrow(() => dismissDevNote({ local: broken, session: broken, todayKst: "2026-09-22", hideToday: true }));
});

// --- 10.08 노트가 실제로 바뀐 것을 가리키는지 (2026-10-08) -------------------
// 노트는 사람이 손으로 쓰는 글이라 기능과 어긋나기 쉽다. 특히 **닫아 둔 화면을 광고하는**
// 실수는 실제로 있었다(10.07 노트가 '프로 빌더 열기' 로 보냈다).

test("노트의 길은 모두 앱에 있는 길이다", () => {
  const routes = readFileSync(new URL("../src/App.jsx", import.meta.url), "utf8");
  const paths = CURRENT_NOTE.items.map((item) => item.link).filter(Boolean);
  assert.ok(paths.length >= 2, "길이 하나도 없으면 이 시험이 공허하다");
  for (const link of paths) {
    const [path] = link.split("?");
    assert.match(routes, new RegExp(`path="${path}"`), `${path} 라우트가 앱에 없다`);
  }
});

test("노트의 아이콘 이름이 모두 실제로 있다", () => {
  // 없는 이름을 쓰면 자리만 비고 조용히 지나간다.
  const icons = readFileSync(new URL("../src/components/icons.jsx", import.meta.url), "utf8");
  for (const item of CURRENT_NOTE.items) {
    assert.ok(item.icon, "아이콘 이름이 비었다");
    assert.match(icons, new RegExp(`^\\s{2}${item.icon}:`, "m"), `icons.jsx 에 ${item.icon} 이 없다`);
  }
});

test("노트가 이번에 바뀐 셋을 모두 말한다", () => {
  const all = CURRENT_NOTE.items.map((item) => `${item.title} ${item.text}`).join(" ");
  assert.match(all, /프로 빌더/, "닫아 둔 것을 알려야 한다");
  assert.match(all, /업데이트 중|업데이트 예정/, "왜 못 들어가는지");
  assert.match(all, /들고 있는 것보다|홀딩/, "추천 기준이 바뀐 것");
  assert.match(all, /업비트|빗썸/, "국내 키 발급 안내");
});

test("설명 링크가 그 항목으로 바로 간다", () => {
  const guide = CURRENT_NOTE.items.find((item) => (item.link || "").startsWith("/guide"));
  assert.ok(guide, "사용 설명으로 가는 항목이 있어야 한다");
  assert.equal(guide.link, "/guide?section=domestic-api",
    "/guide 로만 보내면 사용자가 목차에서 다시 찾아야 한다");
});
