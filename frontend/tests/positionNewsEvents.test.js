import assert from "node:assert/strict";
import test from "node:test";

import { positionNewsModule } from "../src/features/agents/positionNews/events.js";

test("article cards link only to the exact signed response that covered their own IDs", () => {
  const state = { receiptLinks: {
    first: { eventId: 10, articleIds: ["old"] }, next: { eventId: 20, articleIds: ["new"] },
  }, data: { context: { session_id: 41 }, items: [
    { id: "old", title: "이전 기사", _activity_receipt_key: "first" },
    { id: "new", title: "새 기사", _activity_receipt_key: "next" },
    { id: "not-signed", title: "서명에 포함되지 않은 기사", _activity_receipt_key: "next" },
  ] } };
  const rows = positionNewsModule.buildEvents({ featureStates: { position_news: state } });
  assert.deepEqual(rows.map((row) => row.activityEventId), [10, 20, null]);
  assert.deepEqual(rows.map((row) => row.activitySessionId), [41, 41, null]);
});

test("successful empty collection does not create a chat notification", () => {
  const events = positionNewsModule.buildEvents({ featureStates: { position_news: {
    data: { context: { asset_symbol: "NEW" }, items: [], analysis_status: "empty", collection: { status: "ready" } },
  } } });
  assert.deepEqual(events, []);
});

test("failed news requests are visible inside the activity stream", () => {
  const [event] = positionNewsModule.buildEvents({ featureStates: { position_news: {
    status: "error", error: "offline", data: null,
  } } });
  assert.ok(event);
  assert.match(event.title, /뉴스.*연결/);
});

test("pending position news waits quietly until an article arrives", () => {
  const events = positionNewsModule.buildEvents({
    featureStates: {
      position_news: {
        data: {
          context: {
            session_id: 2,
            asset_symbol: "EDEN",
            coin_name: "EDEN",
            position_side: "long",
          },
          overview: {
            text: "EDEN 공용 뉴스 수집을 준비하고 있어요.",
          },
          items: [],
          analysis_status: "pending",
          updated_at: null,
          collection: {
            status: "pending",
            freshness: "pending",
          },
        },
      },
    },
  });

  assert.deepEqual(events, []);
});

test("position news shows the article title and content summary without position labels", () => {
  const events = positionNewsModule.buildEvents({
    featureStates: {
      position_news: {
        data: {
          context: {
            session_id: 2,
            asset_symbol: "EDEN",
            coin_name: "EDEN",
            position_side: "long",
          },
          overview: {
            text: "EDEN 최근 헤드라인을 다시 확인했어요.",
          },
          items: [{
            id: "news-1",
            title: "OpenEden, 토큰화 미국 국채 플랫폼 확대",
            source: "CoinDesk",
            url: "https://news.example.com/openeden",
            published: "2026-08-25T01:11:44Z",
            position_effect: "unclear",
            summary: "OpenEden이 토큰화 미국 국채 플랫폼의 지원 범위를 확대했다는 내용입니다.",
          }],
          analysis_status: "rate_limited",
          updated_at: "2026-08-25T00:48:37Z",
          collection: {
            status: "ready",
            freshness: "fresh",
            last_success_at: "2026-08-25T01:11:44Z",
          },
        },
      },
    },
  });

  assert.equal(events.length, 1);
  assert.equal(events[0].title, "OpenEden, 토큰화 미국 국채 플랫폼 확대");
  assert.equal(
    events[0].summary,
    "OpenEden이 토큰화 미국 국채 플랫폼의 지원 범위를 확대했다는 내용입니다.",
  );
  assert.ok(!("detail" in events[0]));
  assert.equal(events[0].occurredAt, "2026-08-25T01:11:44Z");
  assert.equal(events[0].sourceLabel, "CoinDesk");
  const serialized = JSON.stringify(events);
  assert.doesNotMatch(serialized, /롱 포지션|유리한 뉴스|불리한 뉴스|판단 근거|최근 헤드라인 요약/);
});

test("English headlines and summaries cannot enter the agent notification stream", () => {
  const events = positionNewsModule.buildEvents({ featureStates: { position_news: { data: {
    items: [
      { id: "pending", title: "Bitcoin rises", summary: "비트코인 상승" },
      { id: "ready", title: "비트코인 상승", summary: "Bitcoin rises" },
    ],
  } } } });
  assert.equal(events.length, 1);
  assert.equal(events[0].id, "position-news-ready");
  assert.equal(events[0].summary, "");
});
