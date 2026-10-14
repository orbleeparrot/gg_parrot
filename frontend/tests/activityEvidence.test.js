import assert from "node:assert/strict";
import test from "node:test";
import { activityLogLink, activityEventId, observationForEvent, createObservationSender } from "../src/features/agents/activityEvidence.js";
import { riskModule } from "../src/features/agents/modules/risk.js";

const event = { id: "risk-41-one", module: "risk", title: "평가손실 주의", summary: "현재 평가손익 -3.00%",
  observationId: "9cf35a06-72f8-47c6-8a4e-61c1ac39361e", observedAt: Date.parse("2026-10-14T01:00:00Z"),
  sourceLabel: "현재 보유 포지션" };

test("risk observations name the actual selected chart exchange, not a hard-coded Binance source", () => {
  const context = { candles: [{ t: 1, o: 100, h: 104, l: 99, c: 100, closed: true }], interval: "1h" };
  assert.equal(riskModule.buildEvents({ ...context, macro: { exchange: "upbit" } })[0].sourceLabel, "업비트 확정 봉");
  assert.equal(riskModule.buildEvents({ ...context, macro: { exchange: "bithumb" } })[0].sourceLabel, "빗썸 확정 봉");
  assert.equal(riskModule.buildEvents(context)[0].sourceLabel, "공개 확정 봉 · 거래소 미기록");
});

test("canonical links require actual positive integer identities without time/message inference", () => {
  assert.equal(activityLogLink(41, 123), "/agents?session=41&panel=logs&event=123");
  for (const invalid of [null, undefined, 0, -1, 1.5, "1e2", "x", {}, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(activityLogLink(41, invalid), "");
    assert.equal(activityLogLink(invalid, 123), "");
  }
  assert.equal(activityEventId({ activityEventId: 123, activitySessionId: 42 }, 41), null);
});

test("browser observations retain actual observation identity and never borrow order IDs", () => {
  const payload = observationForEvent(event);
  assert.equal(payload.client_event_id, event.observationId);
  assert.equal(payload.event_name, "browser.risk_observed");
  assert.equal(payload.observed_at, "2026-10-14T01:00:00.000Z");
  assert.equal(payload.details.title, event.title);
  assert.equal(payload.details.module, "risk");
  assert.equal("operation_id" in payload, false);
  assert.equal(observationForEvent({ ...event, module: "runner_log", activityEventId: 22 }), null);
  assert.equal(observationForEvent({ ...event, module: "position_news", isNewsArticle: true }), null);
  assert.equal(observationForEvent({ ...event, observationId: "" }), null);
  assert.equal(observationForEvent({ ...event, module: "strategy" }).event_name, "browser.strategy_reference_observed");
});

test("observations retry at most twice with immutable payload and same id, then require manual retry", async () => {
  const requests = [], statuses = [];
  const sender = createObservationSender({ send: async (sid, payload) => { requests.push({ sid, payload }); throw new Error("offline"); },
    onStatus: (status) => statuses.push(status), wait: async () => {} });
  await sender.submit(41, observationForEvent(event));
  await sender.submit(41, observationForEvent(event));
  assert.equal(requests.length, 2);
  assert.equal(requests[0].payload.client_event_id, requests[1].payload.client_event_id);
  assert.equal(statuses.at(-1).status, "error");
  await sender.submit(41, observationForEvent(event), { retry: true });
  assert.equal(requests.length, 4);
  sender.stop();
});

test("manual retry reuses the first observed payload even when the card title, summary and time changed", async () => {
  const requests = [];
  let offline = true;
  const sender = createObservationSender({ send: async (sid, payload) => {
    requests.push({ sid, payload });
    if (offline) throw new Error("offline");
    return { ok: true, event_id: 123, event: { id: 123, session_id: sid,
      event_key: `browser:${payload.client_event_id}`, component: "browser_observation", event_name: payload.event_name } };
  }, wait: async () => {} });
  const first = observationForEvent(event);
  const expected = JSON.parse(JSON.stringify(first));
  await sender.submit(41, first);
  first.details.title = "바뀐 카드 제목";
  first.details.summary = "바뀐 카드 요약";
  first.observed_at = "2026-10-15T00:00:00.000Z";
  offline = false;
  await sender.submit(41, first, { retry: true });
  assert.equal(requests.length, 3);
  assert.deepEqual(requests.map((request) => request.payload), [expected, expected, expected]);
  sender.stop();
});

test("only canonical matching returned events can attach a log link", async () => {
  const statuses = [];
  const sender = createObservationSender({ send: async () => ({ ok: true, event_id: 123,
    event: { id: 123, session_id: 41, event_key: `browser:${event.observationId}`,
      component: "browser_observation", event_name: "browser.risk_observed" } }), onStatus: (state) => statuses.push(state) });
  await sender.submit(41, observationForEvent(event));
  assert.equal(statuses.at(-1).eventId, 123);
  assert.equal(statuses.at(-1).status, "ready");
  const invalid = createObservationSender({ send: async () => ({ ok: true, event_id: 999,
    event: { id: 999, session_id: 42, event_key: `browser:${event.observationId}` } }), onStatus: (state) => statuses.push(state), wait: async () => {} });
  await invalid.submit(41, observationForEvent(event));
  assert.equal(statuses.at(-1).status, "error");
  assert.equal(statuses.at(-1).eventId, undefined);
  sender.stop(); invalid.stop();
});

test("matching IDs alone cannot relabel a server or different browser event as this observation", async () => {
  for (const changed of [{ component: "engine" }, { event_name: "browser.execution_state_observed" }]) {
    const states = [];
    const sender = createObservationSender({ send: async () => ({ ok: true, event_id: 123, event: {
      id: 123, session_id: 41, event_key: `browser:${event.observationId}`,
      component: "browser_observation", event_name: "browser.risk_observed", ...changed,
    } }), onStatus: (state) => states.push(state), wait: async () => {} });
    await sender.submit(41, observationForEvent(event));
    assert.equal(states.at(-1).status, "error");
    assert.equal(states.at(-1).eventId, undefined);
    sender.stop();
  }
});
