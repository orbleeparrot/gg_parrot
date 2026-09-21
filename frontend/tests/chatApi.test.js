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
  await api.chatList({ afterId: 42 });
  await api.chatList({ metadataOnly: true, seenId: 42 });
  await api.chatList({ messageIds: [2, 3] });
  assert.deepEqual(JSON.parse(calls[0].body), { text: "hello" });
  assert.equal(calls[0].method, "POST");
  assert.deepEqual(JSON.parse(calls[1].body), { last_seen_id: 42 });
  assert.equal(calls[1].method, "PUT");
  const query = new URL(calls[2].url, "https://fixture.invalid").searchParams;
  assert.equal(query.get("before_id"), "81");
  assert.equal(query.get("seen_id"), "0");
  assert.equal(calls[2].timeoutMs, undefined, "internal timeout options never reach fetch");
  assert.equal(new URL(calls[3].url, "https://fixture.invalid").searchParams.get("after_id"), "42");
  assert.equal(new URL(calls[4].url, "https://fixture.invalid").searchParams.get("metadata_only"), "true");
  assert.equal(new URL(calls[5].url, "https://fixture.invalid").searchParams.get("message_ids"), "2,3");
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

test("chat and room requests carry room_id", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, ...options });
    return new Response(JSON.stringify({ items: [] }), { status: 200 });
  });
  await api.chatList({ roomId: 12, afterId: 3 });
  await api.chatPost("hi", { roomId: 12 });
  await api.chatRead(9, { roomId: 12 });
  await api.chatPost("public");
  await api.roomsList();
  await api.roomCreate({ title: "t", capacity: 3, entry_fee: 50, consent: true });
  await api.roomJoin(12);
  await api.roomLeave(12);
  await api.roomExtend(12);
  const q = new URL(calls[0].url, "https://fixture.invalid").searchParams;
  assert.equal(q.get("room_id"), "12");
  assert.equal(q.get("after_id"), "3");
  assert.deepEqual(JSON.parse(calls[1].body), { text: "hi", room_id: 12 });
  assert.deepEqual(JSON.parse(calls[2].body), { last_seen_id: 9, room_id: 12 });
  assert.deepEqual(JSON.parse(calls[3].body), { text: "public" });
  assert.equal(calls[4].url, "/api/rooms");
  assert.equal(calls[5].method, "POST");
  assert.deepEqual(JSON.parse(calls[5].body), { title: "t", capacity: 3, entry_fee: 50, consent: true });
  assert.equal(calls[6].url, "/api/rooms/12/join");
  assert.equal(calls[7].method, "DELETE");
  assert.equal(calls[7].url, "/api/rooms/12/leave");
  assert.equal(calls[8].url, "/api/rooms/12/extend");
});

test("prices request is a public read with comma-joined symbols", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push({ url, ...options }); return new Response(JSON.stringify({ prices: {} }), { status: 200 }); });
  await api.prices(["BTCUSDT", "ETHUSDT"]);
  const u = new URL(calls[0].url, "https://fixture.invalid");
  assert.equal(u.pathname, "/api/prices");
  assert.equal(u.searchParams.get("symbols"), "BTCUSDT,ETHUSDT");
  assert.equal(calls[0].credentials, "omit");
});
