import assert from "node:assert/strict";
import test from "node:test";

import { leaderModule } from "../src/features/agents/modules/leader.js";
import { advanceActivityTimeline } from "../src/features/agents/activityTimeline.js";

const previousTime = Date.parse("2026-09-08T05:00:00Z");
const observedTime = previousTime + 600_000;
const change = {
  id: "onchain:PEPE:2", occurred_at: observedTime, previous_observed_at: previousTime,
  increased_count: 3, decreased_count: 2, compared_count: 47, tracked_count: 49,
  source: "blockscout", source_label: "Blockscout PEPE 보유 지갑",
  source_url: "https://eth.blockscout.com/token/0x6982508145454Ce325dDbE47a25d4ec3d2311933",
  scope: "PEPE 토큰 상위 보유 지갑", daily_source: false,
};
const onchain = (items = [change], overrides = {}) => ({
  status: "ready", coin: "PEPE", observed_at: observedTime, items, ...overrides,
});
const snapshot = (holderData, overrides = {}) => ({
  status: "empty", symbol: "PEPEUSDT", market: "spot", items: [], onchain: holderData, ...overrides,
});
const events = (data) => leaderModule.buildEvents({ featureStates: { whale_activity: { data } } });

test("holder changes show neutral counts, their source, and the comparison window", () => {
  const [event] = events(snapshot(onchain()));
  assert.ok(event);
  assert.equal(event.id, change.id);
  assert.equal(event.title, "PEPE 지갑 잔고 변화");
  assert.match(event.summary, /47개.*증가 3개.*감소 2개/);
  assert.doesNotMatch(event.title + event.summary, /매수|매도|체결|담는|파는/);
  assert.equal(event.occurredAt, observedTime);
  assert.equal(event.sourceLabel, change.source_label);
  assert.equal(event.sourceUrl, change.source_url);
  assert.equal(event.detailLabel, "비교 기간");
  assert.match(event.detail, /2026.*09.*08.*14:00.*14:10/);
});

test("initial baselines, unsupported coins, pending snapshots and unchanged balances stay quiet", () => {
  for (const status of ["baseline", "unsupported", "pending", "unavailable", "stopped"]) {
    assert.deepEqual(events(snapshot(onchain([change], { status }))), []);
  }
  assert.deepEqual(events(snapshot(onchain([]))), []);
  assert.deepEqual(events(snapshot(onchain([{ ...change, increased_count: 0, decreased_count: 0 }]))), []);
  assert.deepEqual(events(snapshot(onchain(), { status: "stopped" })), []);
});

test("holder observations reject invalid counts, identities and comparison periods", () => {
  for (const invalid of [
    { id: "" }, { id: "onchain:XRP:2" }, { occurred_at: "not-a-date" },
    { previous_observed_at: observedTime }, { previous_observed_at: null },
    { increased_count: -1 }, { increased_count: 1.5 }, { increased_count: Infinity },
    { increased_count: true }, { decreased_count: null }, { compared_count: 4 },
    { tracked_count: 46 },
  ]) {
    assert.deepEqual(events(snapshot(onchain([{ ...change, ...invalid }]))), [], JSON.stringify(invalid));
  }
});

test("WETH and XRP events disclose the observed asset and the daily source cadence", () => {
  const [weth] = events(snapshot(onchain([{ ...change, id: "onchain:WETH:2",
    scope: "WETH 토큰 상위 보유 지갑", source_label: "Blockscout WETH 보유 지갑",
  }], { coin: "WETH" }), { symbol: "ETHUSDT" }));
  assert.equal(weth.title, "WETH 지갑 잔고 변화");
  assert.match(weth.summary + weth.detail, /WETH 토큰/);
  const [xrp] = events(snapshot(onchain([{ ...change, id: "onchain:XRP:2", daily_source: true,
    source: "xrpscan", source_label: "XRPScan 상위 잔고", source_url: "https://xrpscan.com/balances",
    scope: "XRP 상위 보유 계정", previous_observed_at: observedTime - 86_400_000,
  }], { coin: "XRP", daily_source: true })));
  assert.match(xrp.summary + xrp.detail, /일 단위/);
  assert.equal(xrp.sourceUrl, "https://xrpscan.com/balances");
});

test("valid holder observations remain visible during Binance outages without changing trade events", () => {
  const output = events(snapshot(onchain(), { status: "unavailable", quote_asset: "USDT", items: [{
    id: 15, side: "sell", notional: 150_000, quantity: 1000, price: 150, occurred_at: observedTime,
  }] }));
  assert.equal(output.length, 3);
  assert.equal(output[0].conditionKey, "whale-connection");
  assert.equal(output[1].title, "PEPEUSDT 대규모 매도 체결");
  assert.equal(output[2].id, change.id);
});

test("holder source links only point to the configured public explorers", () => {
  for (const source_url of ["javascript:alert(1)", "https://eth.blockscout.com.evil.invalid/", "http://xrpscan.com/", "https://user:pass@eth.blockscout.com/"]) {
    const [event] = events(snapshot(onchain([{ ...change, source_url }])));
    assert.equal(event.sourceUrl, "");
    assert.equal(event.title, "PEPE 지갑 잔고 변화");
  }
});

test("repeated, missing and reordered holder snapshots never announce the same change twice", () => {
  const session = { session_id: 11, status: "running", connected: true };
  const advance = (previous, data) => advanceActivityTimeline(previous, {
    session, candles: [], receivedAt: observedTime + 1000,
    featureStates: { whale_activity: { data: snapshot(data) } },
  });
  const nextChange = { ...change, id: "onchain:PEPE:3", occurred_at: observedTime + 600_000,
    previous_observed_at: observedTime, increased_count: 1, decreased_count: 0 };
  let timeline = advance(null, onchain());
  for (let iteration = 0; iteration < 10; iteration++) {
    timeline = advance(timeline, onchain([], { status: "pending" }));
    timeline = advance(timeline, onchain([nextChange, change], { observed_at: observedTime + iteration }));
    timeline = advance(timeline, onchain([change, nextChange]));
  }
  assert.deepEqual(timeline.events.map((event) => event.id), [change.id, nextChange.id]);
  assert.deepEqual(timeline.events.map((event) => event.occurredAt), [observedTime, observedTime + 600_000]);
});
