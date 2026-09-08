import assert from "node:assert/strict";
import test from "node:test";
import { countNewObservations, positionNewsNotice, publicationLabel, publicationTime } from "../src/features/agents/positionNews/presentation.js";
import { positionNewsModule } from "../src/features/agents/positionNews/events.js";

test("publication labels show the actual full date in KST without inventing unknown dates", () => {
  assert.equal(publicationLabel("2022-01-02T01:30:00Z"), "게시 2022.01.02 10:30 KST");
  assert.equal(publicationLabel("2022-01-02T10:30:00+09:00"), "게시 2022.01.02 10:30 KST");
  for (const missing of [null, undefined, "", "not-a-date", 0, Infinity, 1e20]) {
    assert.equal(publicationLabel(missing), "게시일 확인 불가");
    assert.equal(publicationTime(missing), null);
  }
});

test("searching, translating, no articles and archived results have distinct fixed notices", () => {
  const searching = positionNewsNotice({ status: "ready", data: { analysis_status: "pending", items: [] } });
  const translating = positionNewsNotice({ status: "ready", data: { translation: { status: "partial", pending_count: 2 }, items: [] } });
  const empty = positionNewsNotice({ status: "ready", data: { analysis_status: "empty", items: [] } });
  const archive = positionNewsNotice({ status: "ready", data: { content_scope: "archive", items: [{ title: "과거 소식" }] } });
  assert.match(searching, /찾고 있어요/);
  assert.match(translating, /2건.*번역 중/);
  assert.match(empty, /수집한 관련 기사가 없어요/);
  assert.match(archive, /과거 관련 기사/);
  assert.equal(new Set([searching, translating, empty, archive]).size, 4);
  assert.equal(positionNewsNotice({ status: "loading", data: { items: [{ title: "새 소식" }] } }), "");
});

test("stale and failed collection never claim there are no matching articles", () => {
  assert.match(positionNewsNotice({ status: "error", data: null }), /지연/);
  const stale = { status: "ready", data: { items: [], collection: { freshness: "stale" } } };
  assert.match(positionNewsNotice(stale), /지연/);
  assert.doesNotMatch(positionNewsNotice(stale), /기사가 없어요/);
  assert.match(positionNewsNotice(stale, { hasArticles: true }), /게시일/);
});

test("archived articles remain events while new-observation counts only include new live articles", () => {
  const state = { data: { content_scope: "mixed", items: [
    { id: "old", title: "과거 프로젝트 출시 소식", published: "2022-01-02T01:30:00Z", is_historical: true },
    { id: "live", title: "오늘 프로젝트 업데이트" },
  ] } };
  const events = positionNewsModule.buildEvents({ featureStates: { position_news: state } });
  assert.equal(events.length, 2);
  assert.equal(events[0].isHistorical, true);
  assert.equal(events[0].notify, false);
  assert.equal(events[0].publishedAt, Date.parse(state.data.items[0].published));
  assert.equal(events[1].notify, true);
  assert.equal(events[1].publishedAt, null);
  assert.equal(countNewObservations(events, new Set()), 1);
  assert.equal(countNewObservations(events, new Set(["position-news-live"])), 0);
  assert.equal(countNewObservations([...events, { id: "risk-alert" }], new Set(["position-news-live"])), 1);
});
