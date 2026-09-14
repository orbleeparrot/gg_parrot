import assert from "node:assert/strict";
import test from "node:test";
import { createRunnerKeyStore } from "../src/lib/runnerKeyStore.js";
import { api } from "../src/api.js";

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

test("rotating the header key updates the security panel and coalesces duplicate rotations", async () => {
  const rotation = deferred();
  let reads = 0;
  let rotations = 0;
  const store = createRunnerKeyStore({
    read: async () => { reads += 1; return { key: "old-key" }; },
    regenerate: () => { rotations += 1; return rotation.promise; },
  });
  const values = [[], []];
  const close = values.map((seen) => store.subscribe(() => seen.push(store.getSnapshot("A").data?.key)));
  const firstLoad = store.load("A", () => true);
  assert.equal(store.load("A", () => true), firstLoad);
  await firstLoad;
  assert.equal(reads, 1);
  const firstRotation = store.rotate("A", () => true);
  assert.equal(store.rotate("A", () => true), firstRotation);
  assert.equal(store.getSnapshot("A").regenerating, true);
  rotation.resolve({ key: "new-key" });
  await firstRotation;
  assert.equal(rotations, 1);
  for (const seen of values) assert.equal(seen.at(-1), "new-key");
  close.forEach((unsubscribe) => unsubscribe());
  assert.equal(store.getSnapshot("A").data, null);
});

test("a key read started before rotation cannot restore the invalidated key", async () => {
  const oldRead = deferred();
  const store = createRunnerKeyStore({ read: () => oldRead.promise, regenerate: async () => ({ key: "new-key" }) });
  const pending = store.load("A", () => true);
  await Promise.resolve();
  await store.rotate("A", () => true);
  oldRead.resolve({ key: "old-key" });
  await pending;
  assert.equal(store.getSnapshot("A").data.key, "new-key");
});

test("a newly opened panel does not rejoin an API GET from before rotation", async (t) => {
  const oldRead = deferred();
  let gets = 0;
  t.mock.method(globalThis, "fetch", async (path) => {
    if (path.endsWith("/regenerate")) return new Response(JSON.stringify({ key: "new-key" }));
    gets += 1;
    return gets === 1 ? oldRead.promise : new Response(JSON.stringify({ key: "new-key" }));
  });
  const store = createRunnerKeyStore({
    read: (generation) => api.runnerKey({ requestKey: `runner-key-${generation}` }),
    regenerate: () => api.runnerKeyRegenerate(),
  });
  const pending = store.load("A", () => true);
  await Promise.resolve();
  assert.equal(gets, 1);
  await store.rotate("A", () => true);
  await store.load("A", () => true);
  assert.equal(gets, 2);
  oldRead.resolve(new Response(JSON.stringify({ key: "old-key" })));
  await pending;
  assert.equal(store.getSnapshot("A").data.key, "new-key");
});

test("an old account's late rotation cannot replace the new account's key", async () => {
  let activeAccount = "A";
  const lateRotation = deferred();
  const store = createRunnerKeyStore({ read: async () => ({ key: `${activeAccount}-key` }), regenerate: () => lateRotation.promise });
  await store.load("A", () => activeAccount === "A");
  const pending = store.rotate("A", () => activeAccount === "A");
  await Promise.resolve();
  activeAccount = "B";
  assert.equal(store.getSnapshot("B").data, null);
  await store.load("B", () => activeAccount === "B");
  lateRotation.resolve({ key: "A-new-key" });
  assert.equal(await pending, undefined);
  assert.equal(store.getSnapshot("B").data.key, "B-key");
  assert.equal(store.getSnapshot("A").data, null);
});

test("account switching before a queued rotation starts never regenerates the next account", async () => {
  let current = true;
  let rotations = 0;
  const store = createRunnerKeyStore({ read: async () => ({ key: "A-key" }), regenerate: async () => { rotations += 1; return { key: "bad-key" }; } });
  const pending = store.rotate("A", () => current);
  current = false;
  await pending;
  assert.equal(rotations, 0);
});

test("a closed panel's late request does not retain a key in memory", async () => {
  const lateRead = deferred();
  const store = createRunnerKeyStore({ read: () => lateRead.promise, regenerate: async () => ({ key: "new-key" }) });
  const close = store.subscribe(() => {});
  const pending = store.load("A", () => true);
  await Promise.resolve();
  close();
  lateRead.resolve({ key: "A-key" });
  await pending;
  assert.equal(store.getSnapshot("A").data, null);
});
