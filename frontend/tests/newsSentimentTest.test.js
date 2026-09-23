import assert from "node:assert/strict";
import test from "node:test";
import { createNewsTestRunner, elapsedSeconds } from "../src/lib/newsSentimentTest.js";

const article = (id) => ({ id, scope: "BTC", title: `News ${id}` });
const result = (verdict = "bullish", elapsed_ms = 123.45) => ({ verdict, elapsed_ms, reason: "fixture", model: "semif-test:0.1.1" });
const flush = () => new Promise(setImmediate);
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };

test("one inference at a time; completion is shown before moving to the history", async () => {
  const first = deferred(), second = deferred(), display = deferred();
  let state, calls = 0, active = 0;
  const runner = createNewsTestRunner({ onChange: value => { state = value; },
    fetchFeed: async () => ({ items: [article("one"), article("one"), article("two")], cursor: "next" }),
    analyze: async () => { calls++; active++; assert.equal(active, 1); const value = await (calls === 1 ? first : second).promise; active--; return value; },
    delay: () => display.promise,
  });
  const running = runner.start();
  await flush();
  assert.equal(state.current.article.id, "one");
  assert.equal(calls, 1);
  first.resolve(result());
  await flush();
  assert.equal(state.phase, "complete");
  assert.equal(state.current.result.elapsed_ms, 123.45);
  assert.equal(state.history.length, 0);
  display.resolve();
  await flush();
  assert.equal(state.current.article.id, "two");
  assert.equal(state.history[0].article.id, "one");
  assert.equal(calls, 2);
  runner.stop();
  second.resolve(result("bearish"));
  await running;
  assert.equal(state.count, 1, "late result after stop must not modify the UI");
});

test("failed article is retried on resume and never gets a made-up classification", async () => {
  let state, attempts = 0;
  let runner;
  runner = createNewsTestRunner({ onChange: v => { state = v; },
    fetchFeed: async () => ({ items: [article("one")], cursor: "next" }),
    analyze: async () => { if (++attempts === 1) throw new Error("model unreachable"); return result(); },
    delay: async () => { runner.stop(); },
  });
  await runner.start();
  assert.equal(state.phase, "error");
  assert.equal(state.current.result, null);
  assert.equal(state.count, 0);
  await runner.start();
  assert.equal(attempts, 2);
  assert.equal(state.count, 1);
});

test("empty feed waits; new published article is processed without restarting", async () => {
  let state, reads = 0, runner;
  const cursors = [];
  runner = createNewsTestRunner({ onChange: v => { state = v; },
    fetchFeed: async cursor => { cursors.push(cursor); return ++reads === 1 ? { items: [], cursor: "cursor-1" } : { items: [article("fresh")], cursor: "cursor-2" }; },
    analyze: async () => result(),
    delay: async ms => { if (ms === 3000) assert.equal(state.phase, "waiting"); else runner.stop(); },
  });
  await runner.start();
  assert.deepEqual(cursors, ["", "cursor-1"]);
  assert.equal(state.current.article.id, "fresh");
});

test("history is bounded while total timing stays accurate", async () => {
  let state, n = 0, runner;
  runner = createNewsTestRunner({ onChange: v => { state = v; },
    fetchFeed: async () => ({ items: [article(String(++n))], cursor: String(n) }),
    analyze: async () => result("neutral", 10),
    delay: async () => { if (n === 65) runner.stop(); },
  });
  await runner.start();
  assert.equal(state.count, 65);
  assert.equal(state.totalMs, 650);
  assert.equal(state.history.length, 60);
  runner.reset();
  assert.equal(state.history.length, 0);
  assert.equal(state.current, null);
});

test("stopping during a feed request never starts the model", async () => {
  const feed = deferred();
  let calls = 0;
  const runner = createNewsTestRunner({ onChange: () => {}, fetchFeed: () => feed.promise,
    analyze: async () => { calls++; return result(); } });
  const running = runner.start();
  runner.stop();
  feed.resolve({ items: [article("late")] });
  await running;
  assert.equal(calls, 0);
});

test("format uses real milliseconds, including subsecond measurements", () => {
  assert.equal(elapsedSeconds(85), "0.09");
  assert.equal(elapsedSeconds(1234), "1.23");
});
