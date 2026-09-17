import test from "node:test";
import assert from "node:assert/strict";
import { visitPayload, shouldRecord } from "../src/lib/visit.js";

test("visitPayload strips the query from the path and picks utm_source", () => {
  const payload = visitPayload("/board?x=1", { referrer: "https://www.google.com/search?q=gg", search: "?utm_source=naver&x=1", visitor: "v1" });
  assert.deepEqual(payload, { path: "/board", referrer: "https://www.google.com/search?q=gg", utm_source: "naver", visitor: "v1" });
});

test("shouldRecord dedupes the same path for 30 minutes", () => {
  const store = new Map();
  assert.equal(shouldRecord("/", 0, store), true);
  assert.equal(shouldRecord("/", 10 * 60 * 1000, store), false);
  assert.equal(shouldRecord("/board", 10 * 60 * 1000, store), true);
  assert.equal(shouldRecord("/", 31 * 60 * 1000, store), true);
});
