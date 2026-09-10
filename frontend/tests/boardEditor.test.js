import test from "node:test";
import assert from "node:assert/strict";
import { editorImageKeys, planImageOrder, serializeEditor } from "../src/lib/boardEditor.js";

const el = (nodeName, children = [], attrs = {}) => ({ nodeType: 1, nodeName, childNodes: children, getAttribute: (k) => attrs[k] ?? null });
const text = (data) => ({ nodeType: 3, data });

test("serializeEditor turns lines and inline images into text with image marks", () => {
  const root = el("DIV", [
    text("첫 줄"),
    el("DIV", [el("IMG", [], { "data-key": "new:a" })]),
    el("DIV", [text("둘째 줄")]),
    el("DIV", [el("BR")]),
    el("DIV", [text("셋째 줄"), el("IMG", [], { "data-key": "existing:5" }), text("끝")]),
    el("IMG", [], { "data-key": "gone" }),
  ]);
  const plan = planImageOrder(editorImageKeys(root), ["existing:5", "existing:9"]);
  assert.deepEqual(plan.keepIds, [5]);
  assert.deepEqual(plan.newKeys, ["new:a"]);
  assert.equal(serializeEditor(root, plan.indexOf), "첫 줄\n[사진2]\n둘째 줄\n\n셋째 줄\n[사진1]\n끝");
});

test("planImageOrder numbers kept images first in their original order, then new ones in editor order", () => {
  const plan = planImageOrder(["new:x", "existing:3", "new:y", "existing:1"], ["existing:1", "existing:3"]);
  assert.deepEqual(plan.keepIds, [1, 3]);
  assert.equal(plan.indexOf("existing:1"), 1);
  assert.equal(plan.indexOf("existing:3"), 2);
  assert.equal(plan.indexOf("new:x"), 3);
  assert.equal(plan.indexOf("new:y"), 4);
  assert.equal(plan.indexOf("nope"), null);
});
