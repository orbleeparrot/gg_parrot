import assert from "node:assert/strict";
import test from "node:test";

import { createAdaptivePoller } from "../src/lib/polling.js";
import { createRequestCoordinator } from "../src/lib/requestCoordinator.js";

function fakeTimers() {
  let nextId = 0;
  const scheduled = new Map();
  return {
    setTimer(fn, delay) {
      nextId += 1;
      scheduled.set(nextId, { fn, delay });
      return nextId;
    },
    clearTimer(id) {
      scheduled.delete(id);
    },
    next() {
      const [id, value] = scheduled.entries().next().value || [];
      if (id) scheduled.delete(id);
      return value;
    },
    delays() {
      return [...scheduled.values()].map((value) => value.delay);
    },
  };
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
}

test("adaptive poller never overlaps and backs off after failures", async () => {
  const timers = fakeTimers();
  let calls = 0;
  let release;
  const first = new Promise((resolve) => { release = resolve; });
  const task = async () => {
    calls += 1;
    if (calls === 1) return first;
    if (calls < 4) throw new Error("offline");
    return "ok";
  };
  const poller = createAdaptivePoller({
    task,
    intervalMs: 1_000,
    maxIntervalMs: 8_000,
    jitterRatio: 0,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  poller.start();
  timers.next().fn();
  poller.trigger();
  assert.equal(calls, 1);
  release();
  await flush();
  assert.deepEqual(timers.delays(), [1_000]);

  timers.next().fn();
  await flush();
  assert.deepEqual(timers.delays(), [2_000]);
  timers.next().fn();
  await flush();
  assert.deepEqual(timers.delays(), [4_000]);
  timers.next().fn();
  await flush();
  assert.deepEqual(timers.delays(), [1_000]);
});

test("hidden poller aborts work and resumes immediately when visible", async () => {
  const timers = fakeTimers();
  let receivedSignal;
  const task = ({ signal }) => {
    receivedSignal = signal;
    return new Promise(() => {});
  };
  const poller = createAdaptivePoller({
    task,
    intervalMs: 1_000,
    jitterRatio: 0,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });

  poller.start();
  timers.next().fn();
  poller.setVisible(false);
  assert.equal(receivedSignal.aborted, true);
  assert.deepEqual(timers.delays(), []);
  poller.setVisible(true);
  assert.deepEqual(timers.delays(), [0]);
});

test("pending collection can refresh promptly then use the ready snapshot cadence", async () => {
  const timers = fakeTimers();
  let calls = 0;
  const poller = createAdaptivePoller({
    task: async () => ({ nextPollMs: ++calls === 1 ? 3_000 : calls === 2 ? 30_000 : null }),
    intervalMs: 30_000,
    jitterRatio: 0,
    setTimer: timers.setTimer,
    clearTimer: timers.clearTimer,
  });
  poller.start();
  timers.next().fn();
  await flush();
  assert.deepEqual(timers.delays(), [3_000]);
  timers.next().fn();
  await flush();
  assert.deepEqual(timers.delays(), [30_000]);
  timers.next().fn();
  await flush();
  assert.deepEqual(timers.delays(), []);
});

test("request coordinator deduplicates consumers and aborts only after all leave", async () => {
  const coordinator = createRequestCoordinator();
  let calls = 0;
  let sharedSignal;
  let resolve;
  const factory = (signal) => {
    calls += 1;
    sharedSignal = signal;
    return new Promise((done) => { resolve = done; });
  };
  const firstController = new AbortController();
  const secondController = new AbortController();
  const first = coordinator.run("GET:user:/api/data", factory, { signal: firstController.signal });
  const second = coordinator.run("GET:user:/api/data", factory, { signal: secondController.signal });

  assert.equal(calls, 1);
  firstController.abort();
  await assert.rejects(first, { name: "AbortError" });
  assert.equal(sharedSignal.aborted, false);

  resolve({ ok: true });
  assert.deepEqual(await second, { ok: true });

  const thirdController = new AbortController();
  const third = coordinator.run("GET:user:/api/slow", factory, { signal: thirdController.signal });
  thirdController.abort();
  await assert.rejects(third, { name: "AbortError" });
  assert.equal(sharedSignal.aborted, true);
});

test("a remounted consumer does not reuse the prior consumer's aborted request", async () => {
  const coordinator = createRequestCoordinator();
  const firstController = new AbortController();
  let calls = 0;
  let releaseOld;
  let releaseFresh;
  const factory = () => {
    calls += 1;
    return calls === 1 ? new Promise((resolve) => { releaseOld = resolve; })
      : new Promise((resolve) => { releaseFresh = resolve; });
  };
  const first = coordinator.run("news", factory, { signal: firstController.signal });
  firstController.abort();
  const remounted = coordinator.run("news", factory);
  await assert.rejects(first, { name: "AbortError" });
  assert.equal(calls, 2);
  releaseOld({ items: ["old"] });
  await flush();
  assert.equal(coordinator.size(), 1);
  const secondConsumer = coordinator.run("news", factory);
  assert.equal(calls, 2);
  releaseFresh({ items: ["fresh"] });
  assert.deepEqual(await remounted, { items: ["fresh"] });
  assert.deepEqual(await secondConsumer, { items: ["fresh"] });
  assert.equal(coordinator.size(), 0);
});
