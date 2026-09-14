import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/api.js";

const flush = () => new Promise(setImmediate);
const html = (status) => new Response("<html>Upstream unavailable</html>", {
  status, headers: { "Content-Type": "text/html" },
});

for (const [label, response] of [
  ["HTML 502", () => html(502)],
  ["JSON 502", () => Response.json({ detail: "Bad gateway" }, { status: 502 })],
  ["HTML 504", () => html(504)],
  ["JSON 504", () => Response.json({ detail: "Gateway timeout" }, { status: 504 })],
  ["HTML 503", () => html(503)],
  ["empty 503", () => new Response("", { status: 503 })],
]) {
  test(`GET recovers from ${label} after one bounded wait`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    const requests = [];
    t.mock.method(globalThis, "fetch", async (path, options) => {
      requests.push({ path, ...options });
      return requests.length === 1 ? response() : Response.json({ items: ["BTCUSDT"] });
    });
    const pending = api.symbols();
    await flush();
    assert.equal(requests.length, 1);
    t.mock.timers.tick(499);
    await flush();
    assert.equal(requests.length, 1);
    t.mock.timers.tick(1);
    assert.deepEqual(await pending, { items: ["BTCUSDT"] });
    assert.equal(requests.length, 2);
    assert.equal(requests[1].signal, requests[0].signal, "one deadline spans the entire retry sequence");
    assert.equal(requests[1].headers.Authorization, undefined);
    assert.equal(requests[1].credentials, "omit");
    assert.equal(requests[1].timeoutMs, undefined);
  });
}

test("exhausted gateway retries preserve the final HTTP status and temporary-error code", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const statuses = [502, 504, 503];
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => html(statuses[calls++]));
  const rejected = assert.rejects(api.myDashboard(), (error) => {
    assert.equal(error.status, 503);
    assert.equal(error.code, "TEMPORARY_SERVER_ERROR");
    assert.match(error.message, /일시적으로 연결/);
    assert.doesNotMatch(error.message, /API 대신 페이지/);
    return true;
  });
  await flush();
  t.mock.timers.tick(500);
  await flush();
  assert.equal(calls, 2);
  t.mock.timers.tick(1499);
  await flush();
  assert.equal(calls, 2);
  t.mock.timers.tick(1);
  await rejected;
  t.mock.timers.tick(10_000);
  assert.equal(calls, 3);
});

for (const [method, action] of [
  ["POST", () => api.login("fixture@example.invalid", "fake-password")],
  ["PUT", () => api.chatRead(1)],
  ["DELETE", () => api.deleteAvatar()],
  ["PATCH multipart", () => api.updateProfile({ username: "fixture", bio: "" })],
]) {
  test(`${method} gateway failures are reported without replaying a mutation`, async (t) => {
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => { calls += 1; return html(502); });
    await assert.rejects(action(), { status: 502, code: "TEMPORARY_SERVER_ERROR" });
    assert.equal(calls, 1);
  });
}

for (const status of [401, 429, 503]) {
  test(`JSON ${status} is returned immediately without gateway retry`, async (t) => {
    let calls = 0;
    t.mock.method(globalThis, "fetch", async () => {
      calls += 1;
      return Response.json({ detail: "Application response" }, { status });
    });
    await assert.rejects(api.myDashboard(), { status, message: "Application response" });
    assert.equal(calls, 1);
  });
}

test("unexpected HTML 200 retains NON_JSON_RESPONSE and is not retried", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls += 1; return html(200); });
  await assert.rejects(api.symbols(), { status: 200, code: "NON_JSON_RESPONSE" });
  assert.equal(calls, 1);
});

test("a fetch TypeError is not blanket-retried", async (t) => {
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls += 1; throw new TypeError("Failed to fetch"); });
  await assert.rejects(api.symbols(), { name: "TypeError", message: "Failed to fetch" });
  assert.equal(calls, 1);
});

test("caller abort cancels the retry wait and prevents a later request", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls += 1; return html(502); });
  const caller = new AbortController();
  const rejected = assert.rejects(api.symbols({ signal: caller.signal }), { name: "AbortError" });
  await flush();
  caller.abort();
  await rejected;
  t.mock.timers.tick(30_000);
  await flush();
  assert.equal(calls, 1);
});

test("deduplicated GET consumers share the same gateway retry", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => ++calls === 1 ? html(502) : Response.json({ ok: true }));
  const first = api.symbols();
  const second = api.symbols();
  await flush();
  assert.equal(calls, 1);
  t.mock.timers.tick(500);
  assert.deepEqual(await Promise.all([first, second]), [{ ok: true }, { ok: true }]);
  assert.equal(calls, 2);
});

test("the overall timeout includes retry waits instead of resetting for each attempt", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  let calls = 0;
  t.mock.method(globalThis, "fetch", async () => { calls += 1; return html(504); });
  const rejected = assert.rejects(api.symbols({ timeoutMs: 750 }), { name: "TimeoutError" });
  await flush();
  t.mock.timers.tick(500);
  await flush();
  assert.equal(calls, 2);
  t.mock.timers.tick(250);
  await rejected;
  t.mock.timers.tick(10_000);
  await flush();
  assert.equal(calls, 2);
});

for (const [method, defaultMs] of [["symbols", 25_000], ["myDashboard", 15_000]]) {
  test(`${method} has a finite default deadline that callers can override`, async (t) => {
    t.mock.timers.enable({ apis: ["setTimeout"] });
    t.mock.method(globalThis, "fetch", (_, { signal }) => new Promise((_, reject) => {
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    }));
    let settled = false;
    const defaultRequest = assert.rejects(api[method](), { name: "TimeoutError" }).then(() => { settled = true; });
    t.mock.timers.tick(defaultMs - 1);
    await flush();
    assert.equal(settled, false);
    t.mock.timers.tick(1);
    await defaultRequest;
    const overrideRequest = assert.rejects(api[method]({ timeoutMs: 100 }), { name: "TimeoutError" });
    t.mock.timers.tick(100);
    await overrideRequest;
  });
}
