import assert from "node:assert/strict";
import test from "node:test";
import { newsIdentity } from "../src/lib/newsIdentity.js";
import { prepareNewsResponse } from "../src/lib/newsBriefings.js";
import { mergePositionNews } from "../src/features/agents/positionNews/feed.js";

const first = { id: "old", title: "코인 기사", source: "매체", url: "https://example.test/story?id=1" };
const alias = { ...first, id: "alias", source: "example.test", url: "http://EXAMPLE.test:80/story/?id=1&utm_source=rss#top" };

test("publisher aliases dedupe without deleting ID queries, encoded queries or hash routers", () => {
  assert.equal(newsIdentity(first), newsIdentity(alias));
  for (const url of ["https://example.test/story?id=2", "https://example.test/story?id=%B0%A1", "https://example.test/story?id=%B0%A2"]) {
    assert.notEqual(newsIdentity(first), newsIdentity({ ...first, url }));
  }
  assert.notEqual(newsIdentity({ url: "https://example.test/#/1" }), newsIdentity({ url: "https://example.test/#/2" }));
});

test("public dedupe does not turn hidden duplicates into pending translation", () => {
  const result = prepareNewsResponse({ items: [first, alias], translation: { status: "ready" } });
  assert.equal(result.items.length, 1);
  assert.equal(result.translation.pending_count, 0);
  assert.equal(result.translation.status, "ready");
});

test("cursor pages coalesce changed legacy aliases, preserving enrichment and cursor", () => {
  const current = { cursor: 1, items: [first] };
  const changed = { ...alias, community_summary: "보강 결과" };
  const result = mergePositionNews(current, { cursor: 2, items: [changed] });
  assert.equal(result.items.length, 1);
  assert.equal(result.items[0].community_summary, "보강 결과");
  assert.equal(result.cursor, 2);
  assert.equal(current.items.length, 1);
});

test("an empty cursor poll does not flip the order of undated articles", () => {
  const current = { cursor: 1, items: [first, { ...first, id: "second", url: "https://example.test/second" }] };
  const result = mergePositionNews(current, { cursor: 1, items: [] });
  assert.deepEqual(result.items, current.items);
});

test("community post IDs preserve separate same-title posts", () => {
  assert.notEqual(newsIdentity({ ...first, content_type: "community", community_post_id: "1" }),
    newsIdentity({ ...first, content_type: "community", community_post_id: "2" }));
});
