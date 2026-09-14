import assert from "node:assert/strict";
import test from "node:test";
import { mergePositionNews } from "../src/features/agents/positionNews/feed.js";

test("cursor changes append new articles and replace enrichment by stable ID", () => {
  const first = { cursor: 2, items: [{ id: "a", title: "첫 기사", summary: "" }] };
  const next = mergePositionNews(first, { cursor: 4, reset: false,
    items: [{ id: "a", title: "첫 기사", summary: "완료된 요약" }, { id: "b", title: "새 기사" }] });
  assert.equal(next.items.length, 2);
  assert.equal(next.items.find((item) => item.id === "a").summary, "완료된 요약");
  assert.equal(mergePositionNews(next, { cursor: 4, reset: false, items: [] }).items.length, 2);
  assert.equal(first.items[0].summary, "");
});

test("server reset discards stale cursor state", () => {
  const incoming = { cursor: 1, reset: true, items: [{ id: "new" }] };
  assert.deepEqual(mergePositionNews({ cursor: 100, items: [{ id: "old" }] }, incoming), incoming);
});
