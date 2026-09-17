import test from "node:test";
import assert from "node:assert/strict";
import {
  FIRST_SEEN_KEY, SESSION_IDLE_MS, SESSION_KEY, VISITOR_KEY, createVisitTracker, isNewVisitor, sessionState, visitPayload,
} from "../src/lib/visit.js";

function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)), map };
}

function harness({ now = 1_000_000, search = "", referrer = "", innerWidth = 1280, local = {}, session = {}, token = "" } = {}) {
  const calls = [];
  const beacons = [];
  const listeners = { win: new Map(), doc: new Map() };
  const doc = {
    referrer, visibilityState: "visible",
    addEventListener: (name, fn) => listeners.doc.set(name, fn),
  };
  const win = {
    innerWidth, location: { search },
    addEventListener: (name, fn) => listeners.win.set(name, fn),
  };
  const nav = { sendBeacon: (url, blob) => { beacons.push({ url, blob }); return true; } };
  let counter = 0;
  const clock = { now };
  const tracker = createVisitTracker({
    win, doc, nav,
    local: memoryStorage(local), session: memoryStorage(session),
    fetchFn: (url, init) => { calls.push({ url, init, body: JSON.parse(init.body) }); return Promise.resolve({ ok: true }); },
    now: () => clock.now,
    makeKey: () => `k${++counter}`,
    token: () => token,
  });
  const readBeacon = async (b) => JSON.parse(await b.blob.text());
  return { tracker, calls, beacons, listeners, clock, doc, readBeacon };
}

test("sessionState starts a landing session when nothing is stored or the last activity is 30 minutes old", () => {
  const fresh = sessionState(1000, null, () => "new");
  assert.deepEqual(fresh, { key: "new", last_ms: 1000, started_ms: 1000, is_landing: true, utm: "" });
  const stale = sessionState(SESSION_IDLE_MS + 5000, { key: "old", last_ms: 5000, started_ms: 1000 }, () => "new2");
  assert.equal(stale.key, "new2");
  assert.equal(stale.is_landing, true);
  assert.equal(stale.started_ms, SESSION_IDLE_MS + 5000);
});

test("sessionState keeps a live session, refreshes last_ms and carries started_ms + utm", () => {
  const next = sessionState(9000, { key: "old", last_ms: 5000, started_ms: 1000, utm: "kakao" }, () => "unused");
  assert.deepEqual(next, { key: "old", last_ms: 9000, started_ms: 1000, is_landing: false, utm: "kakao" });
  const legacy = sessionState(9000, { key: "old", last_ms: 5000 }, () => "unused");
  assert.equal(legacy.started_ms, 5000, "old records without started_ms fall back to last_ms");
});

test("isNewVisitor is true only when first_seen was created inside the current session", () => {
  assert.equal(isNewVisitor(1000, 1000), true);
  assert.equal(isNewVisitor(1500, 1000), true);
  assert.equal(isNewVisitor(500, 1000), false);
  assert.equal(isNewVisitor(NaN, 1000), false);
});

test("visitPayload follows the server contract: query stripped, utm from the URL, sizes capped", () => {
  const payload = visitPayload("/board?x=1#top", {
    referrer: "https://www.google.com/search?q=gg", search: "?utm_source=naver&x=1", visitor: "v1",
    viewKey: "vk", sessionKey: "sk", isNew: 1, isLanding: 0, screenW: 390.4,
  });
  assert.deepEqual(payload, {
    kind: "view", path: "/board", view_key: "vk", session_key: "sk", referrer: "https://www.google.com/search?q=gg",
    utm_source: "naver", visitor: "v1", is_new: true, is_landing: false, screen_w: 390,
  });
  assert.equal(visitPayload("", {}).path, "/");
  assert.equal(visitPayload("/x", { search: "", utm: "kakao" }).utm_source, "kakao", "session utm is reused when the URL has none");
  assert.equal(visitPayload("/x", { search: "?utm_source=" + "a".repeat(80) }).utm_source.length, 60);
  assert.equal(visitPayload("backtest", { kind: "event" }).kind, "event");
});

test("first view: new visitor id + first_seen + landing session, Authorization only when logged in", () => {
  const h = harness({ search: "?utm_source=kakao", referrer: "https://t.co/abc", innerWidth: 390, token: "tok" });
  h.tracker.recordVisit("/leaderboard?page=2");
  assert.equal(h.calls.length, 1);
  const { url, init, body } = h.calls[0];
  assert.equal(url, "/api/visit");
  assert.equal(init.method, "POST");
  assert.equal(init.keepalive, true);
  assert.equal(init.headers.Authorization, "Bearer tok");
  assert.deepEqual(body, {
    kind: "view", path: "/leaderboard", view_key: "k2", session_key: "k1", referrer: "https://t.co/abc", utm_source: "kakao",
    visitor: "k3", is_new: true, is_landing: true, screen_w: 390,
  });
  assert.equal(h.tracker.currentViewKey(), "k2");

  const anon = harness();
  anon.tracker.recordVisit("/");
  assert.equal(anon.calls[0].init.headers.Authorization, undefined);
});

test("later views in the same session: same session_key, not landing, still is_new; utm sticks to the session", () => {
  const h = harness({ search: "?utm_source=kakao" });
  h.tracker.recordVisit("/");
  h.clock.now += 5 * 60 * 1000;
  h.tracker.recordVisit("/leaderboard");
  const first = h.calls[0].body;
  const second = h.calls[1].body;
  assert.equal(second.session_key, first.session_key);
  assert.equal(second.is_landing, false);
  assert.equal(second.is_new, true);
  assert.notEqual(second.view_key, first.view_key);
  assert.equal(second.utm_source, "kakao");
});

test("returning after 30 minutes idle: new session, landing, not new visitor", () => {
  const h = harness({ local: { [VISITOR_KEY]: "vis", [FIRST_SEEN_KEY]: "10" }, session: { [SESSION_KEY]: JSON.stringify({ key: "old", last_ms: 1_000_000, started_ms: 900_000 }) } });
  h.clock.now = 1_000_000 + SESSION_IDLE_MS + 1;
  h.tracker.recordVisit("/news");
  const body = h.calls[0].body;
  assert.notEqual(body.session_key, "old");
  assert.equal(body.is_landing, true);
  assert.equal(body.is_new, false);
  assert.equal(body.visitor, "vis");
});

test("leave beacon: sent once per view via sendBeacon on hidden, then again for the next view on route change", async () => {
  const h = harness();
  h.tracker.recordVisit("/");
  h.clock.now += 42_000;
  h.doc.visibilityState = "hidden";
  h.listeners.doc.get("visibilitychange")();
  h.listeners.win.get("pagehide")();
  assert.equal(h.beacons.length, 1, "hidden + pagehide send one beacon for the same view");
  assert.equal(h.beacons[0].url, "/api/visit/leave");
  assert.equal(h.beacons[0].blob.type, "application/json");
  assert.deepEqual(await h.readBeacon(h.beacons[0]), { view_key: "k2", dwell_ms: 42_000 });

  h.doc.visibilityState = "visible";
  h.tracker.recordVisit("/leaderboard");
  assert.equal(h.beacons.length, 1, "the first view already left; navigating does not resend it");
  h.clock.now += 7_000;
  h.tracker.recordVisit("/news");
  assert.equal(h.beacons.length, 2, "route change leaves the previous view");
  assert.deepEqual(await h.readBeacon(h.beacons[1]), { view_key: h.calls[1].body.view_key, dwell_ms: 7_000 });
});

test("leave falls back to keepalive fetch when sendBeacon is missing", () => {
  const h = harness();
  h.tracker.recordVisit("/");
  const calls = [];
  const t = createVisitTracker({
    win: { innerWidth: 800, location: { search: "" }, addEventListener() {} }, doc: { referrer: "", addEventListener() {} }, nav: {},
    local: memoryStorage(), session: memoryStorage(),
    fetchFn: (url, init) => { calls.push({ url, init }); return Promise.resolve(); }, now: () => 5000, makeKey: () => "k", token: () => "",
  });
  t.recordVisit("/x");
  t.leave();
  assert.equal(calls[1].url, "/api/visit/leave");
  assert.equal(calls[1].init.keepalive, true);
  assert.deepEqual(JSON.parse(calls[1].init.body), { view_key: "k", dwell_ms: 0 });
});

test("recordEvent posts kind=event with the session key and a fresh view_key", () => {
  const h = harness();
  h.tracker.recordVisit("/builder");
  h.clock.now += 1000;
  h.tracker.recordEvent("backtest");
  const view = h.calls[0].body;
  const event = h.calls[1].body;
  assert.equal(event.kind, "event");
  assert.equal(event.path, "backtest");
  assert.equal(event.session_key, view.session_key);
  assert.notEqual(event.view_key, view.view_key);
  assert.equal(event.is_landing, false);
  assert.equal(h.beacons.length, 0, "an event does not end the current view");
});

test("storage failures never break the beacon", () => {
  const calls = [];
  const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const t = createVisitTracker({
    win: { innerWidth: 800, location: { search: "" }, addEventListener() {} }, doc: { referrer: "", addEventListener() {} }, nav: {},
    local: { getItem: () => null, setItem() {} }, session: { getItem: () => { throw new Error("x"); }, setItem() {} },
    fetchFn: (url, init) => { calls.push({ url, init }); return Promise.resolve(); }, now: () => 1, makeKey: () => "k", token: () => "",
  });
  assert.doesNotThrow(() => t.recordVisit("/"));
  assert.equal(calls.length, 1);
  void throwing;
});
