import assert from "node:assert/strict";
import test from "node:test";

import { api } from "../src/api.js";
import { withRequestTimeout } from "../src/lib/requestTimeout.js";

test("chat requests send only message text and support pagination/read cursors", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, ...options });
    return new Response(JSON.stringify({ items: [] }), { status: 200 });
  });
  await api.chatPost("hello");
  await api.chatRead(42);
  await api.chatList({ beforeId: 81, seenId: 0 });
  assert.deepEqual(JSON.parse(calls[0].body), { text: "hello" });
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(JSON.parse(calls[1].body), { last_seen_id: 42 });
  assert.equal(calls[1].method, "PUT");
  const query = new URL(calls[2].url, "https://fixture.invalid").searchParams;
  assert.equal(query.get("before_id"), "81");
  assert.equal(query.get("seen_id"), "0");
  assert.equal(calls[2].timeoutMs, undefined, "internal timeout options never reach fetch");
});

function waitUntilAborted(signal) {
  return new Promise((_, reject) => {
    if (signal.aborted) reject(signal.reason);
    else signal.addEventListener("abort", () => reject(signal.reason), { once: true });
  });
}

test("a stalled chat request times out and releases its shared request slot", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async (_, { signal }) => {
    calls += 1;
    if (calls === 1) return waitUntilAborted(signal);
    return new Response(JSON.stringify({ items: [{ id: 1 }] }), { status: 200 });
  });
  await assert.rejects(api.chatList({ timeoutMs: 10 }), { name: "TimeoutError" });
  assert.deepEqual(await api.chatList(), { items: [{ id: 1 }] });
  assert.equal(calls, 2);
});

test("caller cancellation stays AbortError and successful requests clear deadlines", async () => {
  const caller = new AbortController();
  const request = withRequestTimeout(waitUntilAborted, { signal: caller.signal, timeoutMs: 1000 });
  caller.abort();
  await assert.rejects(request, { name: "AbortError" });
  let completedSignal;
  assert.equal(await withRequestTimeout(async (signal) => {
    completedSignal = signal;
    return 7;
  }, { timeoutMs: 10 }), 7);
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(completedSignal.aborted, false);
});
