import assert from "node:assert/strict";
import test from "node:test";
import { advanceActivityTimeline } from "../src/features/agents/activityTimeline.js";

const session = { session_id: 8, status: "running", connected: true,
  in_position: true, position_qty: 1, position_side: "long", unrealized_pct: 0.1 };
const context = { session, interval: "1m", candles: [], featureStates: {}, receivedAt: 1000 };
const advance = (state, changes = {}) => advanceActivityTimeline(state, { ...context, ...changes });
const news = (items, updated_at = 1000) => ({ position_news: { data: { items, updated_at } } });

test("repeated heartbeat and snapshot timestamps do not create notifications", () => {
  let state = advance(null);
  const events = state.events;
  for (let i = 0; i < 100; i++) {
    state = advance(state, { receivedAt: 2000 + i, observedAt: 2000 + i,
      session: { ...session, unrealized_pct: i / 100, heartbeat_kst: String(i) } });
    assert.equal(state.events, events);
  }
  assert.deepEqual(state.events, []);
});

test("position entry, exit, repeated entry and termination each appear exactly once", () => {
  let state = advance(null, { session: { ...session, in_position: false } });
  state = advance(state);
  state = advance(state, { session: { ...session, unrealized_pct: 0.2 } });
  assert.deepEqual(state.events.map((e) => e.title), ["포지션 진입"]);
  state = advance(state, { session: { ...session, in_position: false } });
  state = advance(state);
  state = advance(state, { session: { ...session, stopping: true, stop_mode: "close_and_stop" } });
  const stopped = { ...session, status: "stopped", connected: false, in_position: false, note: "청산 완료" };
  state = advance(state, { session: stopped });
  state = advance(state, { session: { ...stopped } });
  assert.deepEqual(state.events.map((e) => e.title), ["포지션 진입", "포지션 청산", "포지션 진입", "청산 후 종료 요청", "실행 종료"]);
  assert.equal(new Set(state.events.map((e) => e.id)).size, 5);
});

test("a connection incident does not repeat on polls but can recur after recovery", () => {
  let state = advance(null);
  for (let incident = 0; incident < 2; incident++) {
    for (let poll = 0; poll < 10; poll++) state = advance(state, { session: { ...session, connected: false } });
    state = advance(state);
  }
  assert.deepEqual(state.events.map((e) => e.title), ["실행기 응답 끊김", "실행기 연결 복구", "실행기 응답 끊김", "실행기 연결 복구"]);
});

test("a stop request and terminal report do not repeat as their heartbeat ages", () => {
  let state = advance(null);
  const stopping = { ...session, stopping: true, stop_mode: "close_and_stop" };
  state = advance(state, { session: stopping });
  state = advance(state, { session: { ...stopping, connected: false, position_qty: 0.5 } });
  const stopped = { ...session, status: "stopped", in_position: false, note: "청산 완료" };
  state = advance(state, { session: stopped });
  state = advance(state, { session: { ...stopped, connected: false, stopping: false, stop_mode: null } });
  assert.deepEqual(state.events.map((e) => e.title), ["청산 후 종료 요청", "실행 종료"]);
});

test("an uncertain-order warning does not repeat on connection or quantity updates", () => {
  const uncertain = { ...session, position_uncertain: true };
  let state = advance(null, { session: uncertain });
  state = advance(state, { session: { ...uncertain, connected: false, position_qty: 0.5 } });
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].title, "포지션 확인 필요");
});

test("loss warnings require a risk threshold or worsening and resist boundary oscillation", () => {
  let state = advance(null);
  const loss = (pct) => { state = advance(state, { session: { ...session, unrealized_pct: pct } }); };
  for (const pct of [-2.01, -1.99, -2.03, -2.1, -3.9, -1.8]) loss(pct);
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].summary, "현재 평가손익 -2.01%");
  loss(-4.1);
  loss(-3.99);
  loss(-4.01);
  assert.equal(state.events.length, 2);
  loss(-1.4); // clear recovery, rather than jitter around -2%
  loss(-2.1);
  assert.equal(state.events.length, 3);
});

test("missing position metrics do not rearm an ongoing loss incident", () => {
  let state = advance(null, { session: { ...session, unrealized_pct: -3 } });
  state = advance(state, { session: { ...session, unrealized_pct: null } });
  state = advance(state, { session: { ...session, unrealized_pct: -3.1 } });
  assert.equal(state.events.length, 1);
});

test("a sustained volatility regime does not alert on every closed bar or chart refresh", () => {
  let state = advance(null);
  const bar = (t, h = 102.5) => [{ t, o: 100, h, l: 100, c: 100, closed: true }];
  for (const t of [1000, 2000, 3000]) state = advance(state, { candles: bar(t) });
  assert.equal(state.events.length, 1);
  state = advance(state, { candles: [] });
  state = advance(state, { candles: bar(3000) });
  assert.equal(state.events.length, 1);
  state = advance(state, { candles: bar(4000, 104.5) });
  assert.equal(state.events.length, 2);
  state = advance(state, { candles: bar(5000, 100.5) });
  state = advance(state, { candles: bar(6000) });
  assert.equal(state.events.length, 3);
});

test("an article keeps its first timestamp and place across refreshes and translation", () => {
  const article = { id: "a", title: "처음 번역한 기사", source: "Source", summary: "기사 요약" };
  let state = advance(null, { featureStates: news([article]) });
  state = advance(state, { receivedAt: 2000, featureStates: news([], 2000) });
  state = advance(state, { receivedAt: 3000, featureStates: news([{ ...article, title: "번역된 기사", summary: "번역" }], 3000) });
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].occurredAt, 1000);
  assert.equal(state.events[0].title, "번역된 기사");
  const events = state.events;
  state = advance(state, { receivedAt: 4000, featureStates: news([{ ...article, title: "번역된 기사", summary: "번역" }], 4000) });
  assert.equal(state.events, events);
});

test("seen news survives disappearing responses and more than one screen of messages", () => {
  const articles = Array.from({ length: 35 }, (_, i) => ({ id: `a-${i}`, title: `새 기사 ${i}`, published: 1000 + i }));
  let state = advance(null, { featureStates: news(articles) });
  state = advance(state, { featureStates: news([]) });
  state = advance(state, { featureStates: news([...articles].reverse()) });
  assert.equal(state.events.length, 35);
  assert.deepEqual(state.events.map((e) => e.title), articles.map((a) => a.title));
});

test("a repeated source failure is one incident until the source recovers", () => {
  const unavailable = (observed_at) => ({ whale_activity: { data: { status: "unavailable", observed_at } } });
  let state = advance(null, { featureStates: unavailable(1000) });
  for (let t = 2000; t < 5000; t += 1000) state = advance(state, { featureStates: unavailable(t) });
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].occurredAt, 1000);
  state = advance(state, { featureStates: { whale_activity: { data: { status: "empty", items: [] } } } });
  state = advance(state, { featureStates: unavailable(6000) });
  assert.equal(state.events.length, 2);
});

test("whale collection retries and stale reads remain one incident until a successful snapshot", () => {
  const response = (data, error = "") => ({ whale_activity: { data, error } });
  let state = advance(null, { featureStates: response(null, "offline") });
  const duringOutage = [
    { status: "pending", items: [], collection: { status: "pending" } },
    { status: "ready", items: [], stale: true },
    { status: "ready", items: [], collection: { freshness: "stale" } },
    { status: "unavailable", items: [] },
    { status: "ready", items: [], collection: { status: "error" } },
    { status: "ready", items: [], collection: { status: "collecting" } },
    null,
  ];
  for (let i = 0; i < 3; i++) {
    for (const data of duringOutage) {
      state = advance(state, { receivedAt: 2000 + i, featureStates: response(data) });
    }
  }
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].occurredAt, 1000);
  state = advance(state, { featureStates: response({ status: "empty", items: [] }) });
  state = advance(state, { featureStates: response({ status: "unavailable", items: [] }) });
  assert.equal(state.events.length, 2);
});

test("durable whale snapshots do not repeat reordered or temporarily missing trades", () => {
  const trade = (id, market = "spot") => ({ id: `${market}:LINKUSDT:${id}`, side: "buy",
    price: 120, quantity: 1000, notional: 120000, occurred_at: 1000 + id });
  const response = (items, observedAt, market = "spot") => ({ whale_activity: { data: {
    status: items.length ? "ready" : "empty", symbol: "LINKUSDT", market,
    quote_asset: "USDT", threshold_quote: 100000, items, observed_at: observedAt,
    collection: { status: "ready", freshness: "fresh", snapshot_id: observedAt },
  } } });
  let state = advance(null, { featureStates: response([trade(1), trade(2)], 1000) });
  const firstIds = state.events.map((event) => event.id);
  state = advance(state, { featureStates: response([], 2000) });
  state = advance(state, { featureStates: response([trade(2), trade(1)], 3000) });
  assert.deepEqual(state.events.map((event) => event.id), firstIds);
  assert.deepEqual(state.events.map((event) => event.occurredAt), [1001, 1002]);
  state = advance(state, { featureStates: response([trade(3), trade(2)], 4000) });
  assert.equal(state.events.length, 3);
  state = advance(state, { featureStates: response([trade(1, "futures")], 5000, "futures") });
  assert.equal(state.events.length, 4);
});

test("news retries do not resolve and re-announce the same connection failure", () => {
  const staleData = { items: [{ id: "cached", title: "저장된 기사" }], collection: { status: "ready", freshness: "fresh" } };
  const response = (status) => ({ position_news: { status, data: staleData } });
  let state = advance(null, { featureStates: response("error") });
  for (let i = 0; i < 10; i++) {
    state = advance(state, { featureStates: response("loading") });
    state = advance(state, { featureStates: response("error") });
  }
  assert.equal(state.events.filter((e) => e.conditionKey === "news-connection").length, 1);
  state = advance(state, { featureStates: response("ready") });
  state = advance(state, { featureStates: response("error") });
  assert.equal(state.events.filter((e) => e.conditionKey === "news-connection").length, 2);
});

test("a different session gets its own baseline without retaining another macro's events", () => {
  let state = advance(null, { featureStates: news([{ id: "a", title: "새 기사" }]) });
  state = advance(state, { session: { ...session, session_id: 9 } });
  assert.equal(state.events.length, 0);
  assert.deepEqual(state.seen, {});
});

test("an untranslated article is first announced only when its Korean translation arrives", () => {
  const original = { id: "late-translation", title: "Bitcoin ETF inflows rise", published: 1000 };
  let state = advance(null, { featureStates: news([original]) });
  assert.equal(state.events.length, 0);
  const translated = { ...original, original_title: original.title, title: "비트코인 ETF 자금 유입 증가" };
  for (let receivedAt = 2000; receivedAt <= 4000; receivedAt += 1000) {
    state = advance(state, { receivedAt, featureStates: news([translated], receivedAt) });
  }
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].title, translated.title);
  assert.equal(state.events[0].occurredAt, 1000);
});

test("historical articles keep identity and publication dates through polls and translation corrections", () => {
  const article = { id: "archive", title: "과거 프로젝트 출시", is_historical: true, published: "2022-01-02T01:30:00Z" };
  let state = advance(null, { featureStates: news([article]) });
  for (let i = 0; i < 10; i++) {
    state = advance(state, { featureStates: news([], 2000 + i) });
    state = advance(state, { featureStates: news([{ ...article, title: "과거 프로젝트 출시 소식" }], 3000 + i) });
  }
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].isHistorical, true);
  assert.equal(state.events[0].notify, false);
  assert.equal(state.events[0].publishedAt, Date.parse(article.published));
  assert.equal(state.events[0].occurredAt, Date.parse(article.published));
});

test("a later verified publication date updates the label without creating another article", () => {
  let state = advance(null, { featureStates: news([{ id: "undated", title: "과거 소식", is_historical: true }]) });
  assert.equal(state.events[0].publishedAt, null);
  state = advance(state, { featureStates: news([{ id: "undated", title: "과거 소식", is_historical: true, published: "2022-01-02T01:30:00Z" }]) });
  assert.equal(state.events.length, 1);
  assert.equal(state.events[0].publishedAt, Date.parse("2022-01-02T01:30:00Z"));
});
