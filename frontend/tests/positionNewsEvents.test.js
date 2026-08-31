import assert from "node:assert/strict";
import test from "node:test";

import { positionNewsModule } from "../src/features/agents/positionNews/events.js";

test("pending position news is shown as a current collection status event", () => {
  const before = Date.now();
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

  assert.equal(events.length, 1);
  assert.equal(events[0].title, "EDEN 뉴스 수집 대기 중");
  assert.match(events[0].summary, /수집을 준비/);
  assert.ok(events[0].occurredAt >= before);
  assert.equal(events[0].sourceLabel, "중앙 뉴스 수집 상태");
});

test("reused position news shows the last successful collection time", () => {
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
          items: [],
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
  assert.equal(events[0].occurredAt, "2026-08-25T01:11:44Z");
  assert.equal(events[0].fallbackTime, "최근 수집 확인");
});
