import test from "node:test";
import assert from "node:assert/strict";
import {
  FIRST_SEEN_KEY, HEARTBEAT_MS, SESSION_IDLE_MS, SESSION_KEY, VIEW_SETTLE_MS, VISITOR_KEY, createVisitTracker, dwellOf,
  heartbeatDue, impressionKey, isNewVisitor, isRepeatCall, isSkippedPath, kstDay, sessionState, visitPayload,
} from "../src/lib/visit.js";

function memoryStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return { getItem: (k) => (map.has(k) ? map.get(k) : null), setItem: (k, v) => map.set(k, String(v)), map };
}
const throwingStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };

// 가짜 시계 + 타이머: tick(ms) 로 시간을 밀면 그 안에 도래한 타이머를 순서대로 실행한다.
function harness({
  now = 1_000_000, search = "", pathname = "", referrer = "", innerWidth = 1280, local = {}, session = {}, token = "",
  localStore = null, sessionStore = null, sendBeacon = true,
} = {}) {
  const calls = [];
  const beacons = [];
  const listeners = { win: new Map(), doc: new Map() };
  const doc = { referrer, visibilityState: "visible", addEventListener: (name, fn) => listeners.doc.set(name, fn) };
  const win = { innerWidth, location: { search, pathname }, addEventListener: (name, fn) => listeners.win.set(name, fn) };
  const nav = sendBeacon ? { sendBeacon: (url, blob) => { beacons.push({ url, blob }); return true; } } : {};
  const clock = { now };
  const timers = new Map();
  let timerId = 0;
  let counter = 0;
  const tracker = createVisitTracker({
    win, doc, nav,
    local: localStore || memoryStorage(local), session: sessionStore || memoryStorage(session),
    fetchFn: (url, init) => { calls.push({ url, init, body: JSON.parse(init.body) }); return Promise.resolve({ ok: true }); },
    now: () => clock.now,
    makeKey: () => `k${++counter}`,
    token: () => token,
    setTimer: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, at: clock.now + ms }); return id; },
    clearTimer: (id) => timers.delete(id),
  });
  const tick = (ms) => {
    const until = clock.now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]);
      clock.now = due[1].at;
      due[1].fn();
    }
    clock.now = until;
  };
  const flush = () => tick(VIEW_SETTLE_MS);
  const visit = (path) => { tracker.recordVisit(path); flush(); };
  const readBeacon = async (b) => JSON.parse(await b.blob.text());
  const hide = () => { doc.visibilityState = "hidden"; listeners.doc.get("visibilitychange")(); };
  const show = () => { doc.visibilityState = "visible"; listeners.doc.get("visibilitychange")(); };
  return { tracker, calls, beacons, listeners, clock, doc, win, timers, tick, flush, visit, readBeacon, hide, show };
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

test("pure helpers: skip list, repeat guard, heartbeat condition, dwell cap, KST day + impression key", () => {
  assert.equal(isSkippedPath("/gallery"), true);
  assert.equal(isSkippedPath("/runner"), true);
  assert.equal(isSkippedPath("/runner?run=1"), true);
  assert.equal(isSkippedPath("/runner/install"), false, "the install page is a real screen");
  assert.equal(isSkippedPath("/leaderboard"), false);

  assert.equal(isRepeatCall(null, "/a", 1000), false);
  assert.equal(isRepeatCall({ pathname: "/a", at: 1000 }, "/a", 1500), true);
  assert.equal(isRepeatCall({ pathname: "/a", at: 1000 }, "/a", 2000), false, "exactly 1s later is a new view");
  assert.equal(isRepeatCall({ pathname: "/a", at: 1000 }, "/b", 1100), false);

  assert.equal(heartbeatDue(null, "visible"), false);
  assert.equal(heartbeatDue({ left: false }, "visible"), true);
  assert.equal(heartbeatDue({ left: true }, "visible"), false);
  assert.equal(heartbeatDue({ left: false }, "hidden"), false);
  assert.equal(heartbeatDue({ left: false }, undefined), true, "no visibility API → assume visible");

  assert.equal(dwellOf(null, 10), 0);
  assert.equal(dwellOf({ startedMs: 100 }, 5100), 5000);
  assert.equal(dwellOf({ startedMs: 100 }, 50), 0);
  assert.equal(dwellOf({ startedMs: 0 }, 7 * 3600 * 1000), 6 * 3600 * 1000);

  // 2026-09-17 23:30 KST = 14:30 UTC · 자정을 넘기면 키의 날짜가 바뀐다.
  const lateEvening = Date.UTC(2026, 8, 17, 14, 30);
  assert.equal(kstDay(lateEvening), "2026-09-17");
  assert.equal(kstDay(lateEvening + 31 * 60 * 1000), "2026-09-18");
  assert.equal(kstDay("nope"), "");
  assert.equal(impressionKey(42, lateEvening), "2026-09-17:42");
  assert.notEqual(impressionKey(42, lateEvening), impressionKey(42, lateEvening + 31 * 60 * 1000));
});

test("first view: new visitor id + first_seen + landing session, Authorization only when logged in", () => {
  const h = harness({ search: "?utm_source=kakao", referrer: "https://t.co/abc", innerWidth: 390, token: "tok" });
  h.tracker.recordVisit("/leaderboard?page=2");
  assert.equal(h.calls.length, 0, "the view waits 400ms before it is sent");
  h.flush();
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
  anon.visit("/");
  assert.equal(anon.calls[0].init.headers.Authorization, undefined);
});

test("later views in the same session: same session_key, not landing, still is_new; utm sticks to the session", () => {
  const h = harness({ search: "?utm_source=kakao" });
  h.visit("/");
  h.tick(5 * 60 * 1000);
  h.visit("/leaderboard");
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
  h.visit("/news");
  const body = h.calls[0].body;
  assert.notEqual(body.session_key, "old");
  assert.equal(body.is_landing, true);
  assert.equal(body.is_new, false);
  assert.equal(body.visitor, "vis");
});

test("deferred view: a pathname change inside 400ms cancels the first view (redirects leave no ghost view)", () => {
  const h = harness();
  h.tracker.recordVisit("/admin");
  h.tick(50);
  h.tracker.recordVisit("/mypage"); // 권한 없는 /admin 이 곧바로 /mypage 로 보낸 경우
  h.flush();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].body.path, "/mypage");
  assert.equal(h.calls[0].body.is_landing, true, "the surviving view is the landing, not the redirect source");

  // 타이머가 돌 때 window.location 이 이미 다른 곳이어도 기록하지 않는다.
  const g = harness({ pathname: "/leaderboard" });
  g.tracker.recordVisit("/gallery");
  g.flush();
  assert.equal(g.calls.length, 0, "/gallery is on the skip list");
  g.win.location.pathname = "/news";
  g.tracker.recordVisit("/leaderboard");
  g.flush();
  assert.equal(g.calls.length, 0, "location moved on before the timer fired");
  g.win.location.pathname = "/leaderboard";
  g.tick(2000);
  g.tracker.recordVisit("/leaderboard");
  g.flush();
  assert.equal(g.calls.length, 1);
});

test("StrictMode double effect: the same path inside 1s is recorded once, a real revisit later is a new view", () => {
  const h = harness();
  h.tracker.recordVisit("/board");
  h.tracker.recordVisit("/board");
  h.flush();
  assert.equal(h.calls.length, 1);
  h.tick(1500);
  h.visit("/board");
  assert.equal(h.calls.length, 2, "1.5s later the same path is a genuine new view");
  assert.equal(h.beacons.length, 1, "the earlier view was left when the new one started");
});

test("leave beacon: sent once per view via sendBeacon on hidden, then again for the next view on route change", async () => {
  const h = harness();
  h.visit("/");
  h.tick(42_000);
  h.hide();
  h.listeners.win.get("pagehide")();
  assert.equal(h.beacons.length, 1, "hidden + pagehide send one beacon for the same view");
  assert.equal(h.beacons[0].url, "/api/visit/leave");
  assert.equal(h.beacons[0].blob.type, "application/json");
  assert.deepEqual(await h.readBeacon(h.beacons[0]), { view_key: "k2", dwell_ms: 42_000 });

  h.show();
  h.visit("/leaderboard");
  assert.equal(h.beacons.length, 2, "coming back resumes the first view; navigating away then leaves it again (server keeps the max)");
  h.tick(7_000);
  h.visit("/news");
  assert.equal(h.beacons.length, 3, "route change leaves the previous view");
  // 앞 뷰는 다음 뷰가 400ms 뒤에 확정될 때 떠난다 — 그 400ms 는 아직 앞 화면을 보고 있던 시간이라 체류에 든다.
  assert.deepEqual(await h.readBeacon(h.beacons[2]), { view_key: h.calls[1].body.view_key, dwell_ms: 7_000 + VIEW_SETTLE_MS });
});

test("hidden time is not dwell: coming back resumes the same view with the hidden span subtracted", async () => {
  const h = harness();
  h.visit("/news");
  h.tick(15_000);
  h.hide();
  assert.deepEqual(await h.readBeacon(h.beacons[0]), { view_key: "k2", dwell_ms: 15_000 });
  h.tick(20 * 60 * 1000); // 20분 동안 탭을 숨겨 둔다
  h.show();
  h.tick(5_000);
  h.tracker.leave();
  assert.equal(h.beacons.length, 2);
  assert.deepEqual(await h.readBeacon(h.beacons[1]), { view_key: "k2", dwell_ms: 20_000 }, "15s before + 5s after, not 20 minutes");
});

test("heartbeat: every 60s while visible the current dwell goes to /api/visit/leave and the session last_ms is touched", async () => {
  const h = harness();
  h.visit("/board");
  h.tick(HEARTBEAT_MS);
  assert.equal(h.beacons.length, 1);
  assert.deepEqual(await h.readBeacon(h.beacons[0]), { view_key: "k2", dwell_ms: HEARTBEAT_MS });
  h.tick(HEARTBEAT_MS);
  assert.equal(h.beacons.length, 2);
  assert.deepEqual(await h.readBeacon(h.beacons[1]), { view_key: "k2", dwell_ms: 2 * HEARTBEAT_MS });

  // 31분 뒤 클릭 — 하트비트가 last_ms 를 밀어 두어 같은 세션으로 이어진다.
  h.tick(29 * HEARTBEAT_MS);
  h.visit("/board/1");
  assert.equal(h.calls[1].body.session_key, h.calls[0].body.session_key, "heartbeats keep the session alive");
  assert.equal(h.calls[1].body.is_landing, false);

  // 숨긴 동안은 보내지 않고, 다시 보이면 이어서 보낸다.
  const before = h.beacons.length;
  h.hide();
  h.tick(5 * HEARTBEAT_MS);
  assert.equal(h.beacons.length, before + 1, "only the leave beacon while hidden, no heartbeats");
  h.show();
  h.tick(HEARTBEAT_MS);
  assert.equal(h.beacons.length, before + 2);
});

test("leave/heartbeat without a heartbeat would split the session after 30 idle minutes (guard for the touch)", () => {
  const h = harness();
  h.visit("/a");
  h.tick(10_000);
  h.hide(); // leave 가 last_ms 를 지금으로 민다
  h.tick(SESSION_IDLE_MS - 5_000);
  h.show();
  h.visit("/b");
  assert.equal(h.calls[1].body.session_key, h.calls[0].body.session_key, "the leave touch counts as activity");
});

test("leave falls back to keepalive fetch when sendBeacon is missing", () => {
  const h = harness({ sendBeacon: false });
  h.visit("/x");
  h.tracker.leave();
  assert.equal(h.calls[1].url, "/api/visit/leave");
  assert.equal(h.calls[1].init.keepalive, true);
  assert.deepEqual(JSON.parse(h.calls[1].init.body), { view_key: "k2", dwell_ms: 0 });
});

test("recordEvent posts kind=event with the session key and a fresh view_key, logged in or not", () => {
  const h = harness();
  h.visit("/builder");
  h.tick(1000);
  h.tracker.recordEvent("backtest");
  const view = h.calls[0].body;
  const event = h.calls[1].body;
  assert.equal(event.kind, "event");
  assert.equal(event.path, "backtest");
  assert.equal(event.session_key, view.session_key);
  assert.equal(event.visitor, view.visitor);
  assert.notEqual(event.view_key, view.view_key);
  assert.equal(event.is_landing, false);
  assert.equal(h.calls[1].init.headers.Authorization, undefined, "anonymous backtests are still counted, keyed by visitor");
  assert.equal(h.beacons.length, 0, "an event does not end the current view");
});

test("module memory: a throwing sessionStorage still yields one session per tab, a throwing localStorage yields an empty visitor", () => {
  const h = harness({ sessionStore: throwingStorage });
  h.visit("/");
  h.tick(1000);
  h.visit("/news");
  h.tick(1000);
  h.visit("/board");
  const keys = new Set(h.calls.map((c) => c.body.session_key));
  assert.equal(keys.size, 1, "the session key lives in memory once created");
  assert.equal(h.calls[0].body.is_landing, true);
  assert.equal(h.calls[1].body.is_landing, false);
  assert.equal(h.calls[2].body.is_landing, false);
  assert.ok(h.calls.every((c) => c.body.visitor === "k3"), "localStorage works, so the visitor id is real");

  const g = harness({ localStore: throwingStorage });
  g.visit("/");
  g.tick(1000);
  g.visit("/news");
  assert.equal(g.calls.length, 2);
  assert.ok(g.calls.every((c) => c.body.visitor === ""), "no round-trip through storage → empty visitor, never a fresh id per view");
  assert.ok(g.calls.every((c) => c.body.is_new === false), "cannot know it is a first visit, so it is not claimed");
  assert.equal(g.calls[0].body.session_key, g.calls[1].body.session_key);
});

test("module memory: a write-only failure (quota) still reuses one id inside the tab", () => {
  // getItem 은 되지만 setItem 이 조용히 실패하는 저장소 — 저장이 안 됐으니 방문자는 비우고, 세션은 메모리로 잇는다.
  const readOnly = { getItem: () => null, setItem() {} };
  const h = harness({ localStore: readOnly, sessionStore: readOnly });
  h.visit("/");
  h.tick(1000);
  h.visit("/news");
  assert.equal(h.calls[0].body.visitor, "");
  assert.equal(h.calls[1].body.visitor, "");
  assert.equal(h.calls[0].body.session_key, h.calls[1].body.session_key);
  assert.equal(h.calls[1].body.is_landing, false);
});

test("storage failures never break the beacon", () => {
  const h = harness({ localStore: throwingStorage, sessionStore: throwingStorage });
  assert.doesNotThrow(() => h.visit("/"));
  assert.equal(h.calls.length, 1);
  assert.doesNotThrow(() => h.tracker.recordEvent("backtest"));
  assert.equal(h.calls.length, 2);
});

test("a view that starts hidden (cmd-click / new tab) counts nothing until it is shown, then heartbeats", async () => {
  // 백그라운드 탭에서 열린 뷰 — 첫 하트비트가 숨김 판정으로 끝난 뒤 다시 잡히지 않고, resume() 도 left=false 라
  // 돌아가서 숨어 있던 30분 전체가 체류로 들어가던 버그(1 990 000ms). 시작부터 '떠난' 상태로 두면 visible 이 resume 을 탄다.
  const h = harness();
  h.doc.visibilityState = "hidden";
  h.visit("/news");
  assert.equal(h.calls.length, 1, "the view itself is still recorded");
  h.tick(30 * 60 * 1000);
  assert.equal(h.beacons.length, 0, "no heartbeat and no leave while it has never been visible");
  h.show();
  h.tick(3 * 60 * 1000);
  assert.ok(h.beacons.length >= 2, `heartbeats resume once shown (got ${h.beacons.length})`);
  h.tracker.leave();
  const last = await h.readBeacon(h.beacons[h.beacons.length - 1]);
  assert.deepEqual(last, { view_key: "k2", dwell_ms: 180_000 }, "3 visible minutes, not 33");
});
