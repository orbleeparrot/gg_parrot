import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  createRumTransport,
  ratingFor,
  sanitizeRoute,
  startRum,
} from "../src/lib/rum.js";

function createRumHarness(pathname = "/") {
  const observers = [];
  const documentListeners = new Map();
  const windowListeners = new Map();
  const beacons = [];

  class FakePerformanceObserver {
    constructor(callback) {
      this.callback = callback;
      this.options = null;
      this.pending = [];
      this.takeRecordsCalls = 0;
      observers.push(this);
    }

    observe(options) {
      this.options = options;
    }

    disconnect() {}

    takeRecords() {
      this.takeRecordsCalls += 1;
      const records = this.pending;
      this.pending = [];
      return records;
    }

    emit(entries) {
      this.callback({ getEntries: () => entries });
    }

    queue(entries) {
      this.pending.push(...entries);
    }
  }

  const addListener = (listeners, name, listener) => {
    const current = listeners.get(name) || new Set();
    current.add(listener);
    listeners.set(name, current);
  };
  const removeListener = (listeners, name, listener) => {
    listeners.get(name)?.delete(listener);
  };
  const fire = (listeners, name) => {
    for (const listener of listeners.get(name) || []) listener();
  };

  const documentObject = {
    visibilityState: "visible",
    addEventListener(name, listener) {
      addListener(documentListeners, name, listener);
    },
    removeEventListener(name, listener) {
      removeListener(documentListeners, name, listener);
    },
  };
  const windowObject = {
    location: { pathname },
    crypto: { randomUUID: () => "page-test" },
    addEventListener(name, listener) {
      addListener(windowListeners, name, listener);
    },
    removeEventListener(name, listener) {
      removeListener(windowListeners, name, listener);
    },
  };
  const navigatorObject = {
    sendBeacon(url, body) {
      beacons.push([url, body]);
      return true;
    },
  };

  const stop = startRum({
    windowObject,
    documentObject,
    PerformanceObserverClass: FakePerformanceObserver,
    performanceObject: { getEntriesByType: () => [] },
    navigatorObject,
    random: () => 0,
    sampleRate: 1,
  });

  return {
    beacons,
    documentObject,
    observer(type) {
      return observers.find((observer) => observer.options?.type === type);
    },
    hide() {
      documentObject.visibilityState = "hidden";
      fire(documentListeners, "visibilitychange");
    },
    pagehide() {
      fire(windowListeners, "pagehide");
    },
    stop,
    windowObject,
  };
}

async function beaconPayload(harness, index = 0) {
  return JSON.parse(await harness.beacons[index][1].text());
}

test("Core Web Vital ratings use the published boundary values", () => {
  assert.equal(ratingFor("CLS", 0.1), "good");
  assert.equal(ratingFor("CLS", 0.11), "needs-improvement");
  assert.equal(ratingFor("INP", 501), "poor");
  assert.equal(ratingFor("LCP", 2500), "good");
});

test("RUM routes exclude queries, fragments, and identifier-shaped segments", () => {
  assert.equal(sanitizeRoute("/agents/123?session=secret#news"), "/agents/:id");
  assert.equal(
    sanitizeRoute("/share/06a8bc60-c649-7750-8000-a1999aa9cc42"),
    "/share/:id",
  );
});

test("RUM transport prefers sendBeacon and emits bounded anonymous JSON", async () => {
  const beacons = [];
  const transport = createRumTransport({
    navigatorObject: {
      sendBeacon(url, body) {
        beacons.push([url, body]);
        return true;
      },
    },
    fetchFn: () => assert.fail("fetch fallback should not run"),
  });

  await transport({
    page_id: "page-1",
    route: "/agents",
    metrics: [{ name: "CLS", value: 0.01, rating: "good" }],
  });
  assert.equal(beacons[0][0], "/api/observability/rum");
  const body = JSON.parse(await beacons[0][1].text());
  assert.deepEqual(body.metrics, [{ name: "CLS", value: 0.01, rating: "good" }]);
  assert.equal("user" in body, false);
});

test("RUM loads after the initial React render and observes CLS, INP, and LCP", () => {
  const main = readFileSync(new URL("../src/main.jsx", import.meta.url), "utf8");
  const rum = readFileSync(new URL("../src/lib/rum.js", import.meta.url), "utf8");
  assert.match(main, /import\("\.\/lib\/rum\.js"\)/);
  assert.match(main, /requestIdleCallback/);
  assert.match(rum, /layout-shift/);
  assert.match(rum, /setMetric\("CLS",\s*0\)/);
  assert.match(rum, /largest-contentful-paint/);
  assert.match(rum, /type:\s*"event"/);
});

test("navigation vitals stay attributed to the route where RUM started", async () => {
  const harness = createRumHarness("/");
  harness.observer("layout-shift").emit([
    { hadRecentInput: false, startTime: 100, value: 0.12 },
  ]);

  harness.windowObject.location.pathname = "/agents";
  harness.hide();

  assert.equal((await beaconPayload(harness)).route, "/");
  harness.stop();
});

test("pagehide drains queued performance entries before flushing", async () => {
  const harness = createRumHarness("/agents");
  const eventObserver = harness.observer("event");
  eventObserver.queue([{ interactionId: 7, duration: 64 }]);

  harness.pagehide();

  assert.equal(eventObserver.takeRecordsCalls, 1);
  assert.deepEqual(
    (await beaconPayload(harness)).metrics.find((metric) => metric.name === "INP"),
    { name: "INP", value: 64, rating: "good" },
  );
  harness.stop();
});

test("a fast first interaction is still reported as INP", async () => {
  const harness = createRumHarness("/builder");
  const firstInputObserver = harness.observer("first-input");
  assert.ok(firstInputObserver, "first-input fallback observer should be registered");
  firstInputObserver.queue([{ duration: 24 }]);

  harness.hide();

  assert.deepEqual(
    (await beaconPayload(harness)).metrics.find((metric) => metric.name === "INP"),
    { name: "INP", value: 24, rating: "good" },
  );
  harness.stop();
});
