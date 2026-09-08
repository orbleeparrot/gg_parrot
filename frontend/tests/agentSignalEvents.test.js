import assert from "node:assert/strict";
import test from "node:test";

import { leaderModule } from "../src/features/agents/modules/leader.js";
import { strategyModule } from "../src/features/agents/modules/strategy.js";
import { positionNewsModule } from "../src/features/agents/positionNews/events.js";

const newsEvents = (data, state = {}) => positionNewsModule.buildEvents({
  featureStates: { position_news: { data, ...state } },
});
const whaleEvents = (data, state = {}) => leaderModule.buildEvents({
  featureStates: { whale_activity: { data, ...state } },
});
const candles = [
  { t: 1_000, o: 100, h: 100.5, l: 99, c: 100, closed: true },
  { t: 2_000, o: 100, h: 100.5, l: 99, c: 100, closed: true },
  { t: 3_000, o: 100, h: 102, l: 99, c: 101, closed: true },
];
const strategyContext = {
  macro: { rule_type: "I", position_side: "long", params: { k: 0.5 } },
  session: { session_id: 7, position_side: "long" },
  interval: "1m", candles,
};

test("ordinary strategy scans and empty whale polls do not create chat entries", () => {
  assert.deepEqual(strategyModule.buildEvents({ ...strategyContext, candles: candles.slice(0, 2) }), []);
  assert.deepEqual(whaleEvents({ status: "empty", items: [], observed_at: 1000 }), []);
  assert.deepEqual(whaleEvents({ status: "empty", items: [], observed_at: 2000 }), []);
});

test("a strategy signal keeps its identity when older bars shift its array index", () => {
  const original = strategyModule.buildEvents(strategyContext);
  const expanded = strategyModule.buildEvents({ ...strategyContext,
    candles: [{ ...candles[0], t: 500 }, ...candles],
  });
  assert.equal(original.length, 1);
  assert.equal(expanded.length, 1);
  assert.equal(original[0].occurredAt, 3000);
  assert.equal(original[0].id, expanded[0].id);
});

test("strategy identity distinguishes intervals, sessions, and order sides", () => {
  const [original] = strategyModule.buildEvents(strategyContext);
  const [otherInterval] = strategyModule.buildEvents({ ...strategyContext, interval: "5m" });
  const [otherSession] = strategyModule.buildEvents({ ...strategyContext, session: { session_id: 8 } });
  const [short] = strategyModule.buildEvents({ ...strategyContext,
    macro: { ...strategyContext.macro, position_side: "short" },
    session: { session_id: 7, position_side: "short" },
  });
  assert.equal(new Set([original.id, otherInterval.id, otherSession.id, short.id]).size, 4);
});

test("whale events contain actual large trades and no empty-state narration", () => {
  const events = whaleEvents({ status: "ready", symbol: "ENAUSDT", market: "spot", quote_asset: "USDT",
    threshold_quote: 100_000, items: [
      { id: 101, side: "buy", notional: 120_000, quantity: 1000, price: 120, occurred_at: 3000 },
      { id: 102, side: "buy", notional: 12, quantity: 1, price: 12, occurred_at: 4000 },
    ],
  });
  assert.equal(events.length, 1);
  assert.match(events[0].title, /대규모 매수/);
  assert.equal(events[0].occurredAt, 3000);
});

test("persistent whale failures carry a connection condition instead of new observation time", () => {
  const [error] = whaleEvents(null, { error: "offline" });
  assert.equal(error.conditionKey, "whale-connection");
  assert.equal(error.conditionValue, "error");
  assert.equal(error.occurredAt, 0);
  const [stale] = whaleEvents({ stale: true, items: [] });
  assert.equal(stale.conditionKey, "whale-connection");
  assert.equal(stale.conditionValue, "stale");
});

test("pending and empty news responses stay quiet while real articles remain visible", () => {
  for (const status of ["pending", "empty"]) {
    assert.deepEqual(newsEvents({ analysis_status: status, collection: { status }, items: [] }), []);
  }
  const [article] = newsEvents({ analysis_status: "pending", items: [{ id: "stored", title: "에테나 결제 서비스 출시" }] });
  assert.equal(article.title, "에테나 결제 서비스 출시");
});

test("news connection failures remain visible even during an initial pending snapshot", () => {
  const [event] = newsEvents({ analysis_status: "pending", collection: { status: "pending" }, items: [] }, { status: "error" });
  assert.equal(event.conditionKey, "news-connection");
  assert.equal(event.conditionValue, "error");
  assert.equal(event.occurredAt, 0);
  const [stale] = newsEvents({ collection: { freshness: "stale" }, items: [] });
  assert.equal(stale.conditionKey, "news-connection");
  assert.equal(stale.conditionValue, "stale");
});

test("news identity survives translations, reorderings, and snapshot refreshes", () => {
  const first = { title: "에테나 결제 서비스 선보여", original_title: "Ethena launches payments", source: "Publisher", published: "2026-09-07T00:00:00Z" };
  const second = { title: "비텐서 업그레이드 출시", original_title: "Bittensor releases an upgrade", source: "Publisher", published: "2026-09-06T00:00:00Z" };
  const original = newsEvents({ items: [first, second], updated_at: 1000 });
  const translated = newsEvents({ items: [second, { ...first, title: "에테나 결제 서비스 출시" }], updated_at: 2000 });
  assert.equal(original[0].id, translated[1].id);
  assert.equal(original[1].id, translated[0].id);
  assert.notEqual(original[0].id, original[1].id);
});

test("article URLs take precedence over translated titles and stable server IDs remain unchanged", () => {
  const first = newsEvents({ items: [{ url: "https://publisher.example/article", title: "처음 번역한 제목" }] })[0];
  const translated = newsEvents({ items: [{ url: "https://publisher.example/article", title: "번역 제목" }] })[0];
  assert.equal(first.id, translated.id);
  const stable = newsEvents({ items: [{ id: "news-1", url: "https://publisher.example/article", title: "새 기사" }] })[0];
  assert.equal(stable.id, "position-news-news-1");
});

test("undated news keeps its first observation time in the ledger, not each refresh time", () => {
  const first = newsEvents({ items: [{ id: "undated", title: "새 기사" }], updated_at: 1000 })[0];
  const refreshed = newsEvents({ items: [{ id: "undated", title: "수정한 제목" }], updated_at: 2000 })[0];
  assert.equal(first.occurredAt, 0);
  assert.equal(refreshed.occurredAt, 0);
  assert.equal(first.id, refreshed.id);
});
