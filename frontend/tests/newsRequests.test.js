import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const newsPage = readFileSync(
  new URL("../src/pages/News.jsx", import.meta.url),
  "utf8",
);

test("coin news responses are not cached forever across page revisits", () => {
  assert.doesNotMatch(newsPage, /coinNewsRequests/);
  assert.match(newsPage, /api\.newsCoin\(symbol\)/);
});
