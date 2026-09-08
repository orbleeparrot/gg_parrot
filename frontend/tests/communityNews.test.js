import assert from "node:assert/strict";
import test from "node:test";
import { communityPostIdentity, historicalNewsLabel, newsPublishedLabel, newsSourceLabel, prepareNewsResponse } from "../src/lib/newsBriefings.js";
import { positionNewsModule } from "../src/features/agents/positionNews/events.js";
import { advanceActivityTimeline, emptyActivityTimeline } from "../src/features/agents/activityTimeline.js";
import { countNewObservations } from "../src/features/agents/positionNews/presentation.js";

const post = { content_type: "community", source: "Binance Square", community_post_id: "123456789",
  author: "시장기록자", url: "https://www.binance.com/en/square/post/123456789",
  title: "CHIP에 대한 커뮤니티 작성자의 전망", original_title: "A personal outlook on CHIP",
  published: "2026-09-08T01:00:00Z", position_effect: "favorable", summary: "상승을 보장한다는 잘못된 요약" };
const state = (items) => ({ position_news: { status: "ready", data: { items, collection: { status: "ready", freshness: "fresh" } } } });


test("community source label exposes category, platform and author for both readers", () => {
  assert.equal(newsSourceLabel(post), "커뮤니티 · Binance Square · 시장기록자");
  assert.equal(newsSourceLabel({ ...post, author: "" }), "커뮤니티 · Binance Square · 작성자 미상");
  assert.equal(newsSourceLabel({ source: "CoinDesk" }), "CoinDesk");
  assert.equal(historicalNewsLabel({ ...post, is_historical: true, published: "2022-01-02T01:30:00Z" }), "과거 게시글 · 2022.01.02");
});

test("publication labels use the actual ISO timestamp in KST when display metadata is absent", () => {
  assert.equal(newsPublishedLabel(post), "2026.09.08 10:00 KST");
  assert.equal(newsPublishedLabel({ published: "2026-09-08T23:45:00Z" }), "2026.09.09 08:45 KST");
  assert.equal(newsPublishedLabel({ published: "invalid", published_display: "기존 표시" }), "기존 표시");
  assert.equal(newsPublishedLabel({ received_at: "2026-09-08T01:00:00Z" }), "게시일 확인 불가");
  assert.equal(newsPublishedLabel({ published: "invalid" }), "게시일 확인 불가");
});


test("post IDs survive revised translations, backend title IDs and renamed authors", () => {
  const edited = { ...post, id: "new-title-hash", title: "번역이 개선된 게시글", author: "새 이름" };
  assert.equal(communityPostIdentity(post), communityPostIdentity(edited));
  const events = [post, edited].map((item) => positionNewsModule.buildEvents({ featureStates: state([item]) })[0]);
  assert.equal(events[0].id, events[1].id);
  assert.notEqual(communityPostIdentity(post), communityPostIdentity({ ...post, community_post_id: "987654321" }));
  assert.equal(communityPostIdentity({ ...post, community_post_id: "" }), communityPostIdentity({ ...edited, community_post_id: "" }));
});


test("community opinions never become favorable position signals or expose supplied body summaries", () => {
  const event = positionNewsModule.buildEvents({ featureStates: state([post]) })[0];
  assert.equal(event.isCommunityPost, true);
  assert.equal(event.severity, "info");
  assert.equal(event.expression, "curious");
  assert.equal(event.sourceLabel, "커뮤니티 · Binance Square · 시장기록자");
  assert.equal(event.summary, "커뮤니티 작성자의 의견이며 포지션 영향은 확인되지 않았어요.");
  assert.equal(event.notify, true);
  assert.equal(event.publishedAt, Date.parse(post.published));
});


test("repeated post observations update one row and historical posts never increment the badge", () => {
  const context = { session: { session_id: 41 }, featureStates: state([post]), receivedAt: Date.parse(post.published) };
  const first = advanceActivityTimeline(emptyActivityTimeline(41), context);
  const firstPosts = first.events.filter((event) => event.module === "position_news");
  const edited = { ...post, title: "한국어 제목 수정", id: "different-title-hash" };
  const second = advanceActivityTimeline(first, { ...context, featureStates: state([edited]), receivedAt: context.receivedAt + 60000 });
  const secondPosts = second.events.filter((event) => event.module === "position_news");
  assert.equal(firstPosts.length, 1);
  assert.equal(secondPosts.length, 1);
  assert.equal(secondPosts[0].title, edited.title);
  assert.equal(countNewObservations(secondPosts, new Set(firstPosts.map((event) => event.id))), 0);
  const archive = positionNewsModule.buildEvents({ featureStates: state([{ ...post, is_historical: true }]) })[0];
  assert.equal(archive.notify, false);
  assert.equal(countNewObservations([archive], new Set()), 0);
});


test("English community titles stay pending while original title and body never become display fallbacks", () => {
  const untranslated = { ...post, title: "A personal outlook on CHIP", excerpt: "Original English body" };
  const payload = prepareNewsResponse({ items: [untranslated] });
  assert.equal(payload.items.length, 0);
  assert.equal(payload.translation.pending_count, 1);
  assert.equal(positionNewsModule.buildEvents({ featureStates: state([untranslated]) }).length, 0);
});
