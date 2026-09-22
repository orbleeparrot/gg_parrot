import assert from "node:assert/strict";
import test from "node:test";
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
