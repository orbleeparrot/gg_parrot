import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/api.js";
import { clearAuth, setAuth } from "../src/lib/auth.js";

function member(t) {
  const values = new Map();
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  } });
  setAuth("avatar-member-token", { id: 7, username: "회원" });
  t.after(() => {
    clearAuth();
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  });
}

test("avatar mutations send the authenticated member and preserve multipart boundaries", async (t) => {
  member(t);
  const calls = [];
  const user = { id: 7, avatar_url: "/api/avatars/7?v=uploaded" };
  t.mock.method(globalThis, "fetch", async (url, options) => {
    calls.push({ url, ...options });
    return Response.json({ user });
  });
  const file = new File(["photo bytes"], "photo.png", { type: "image/png" });
  assert.deepEqual(await api.uploadAvatar(file), { user });
  await api.deleteAvatar();
  assert.equal(calls[0].url, "/api/me/avatar");
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].headers.Authorization, "Bearer avatar-member-token");
  assert.equal(calls[0].headers["Content-Type"], undefined);
  assert.deepEqual([...calls[0].body.keys()], ["image"]);
  assert.equal(calls[0].body.get("image").name, "photo.png");
  assert.equal(calls[1].method, "DELETE");
  assert.equal(calls[1].headers.Authorization, "Bearer avatar-member-token");
});

for (const action of ["uploadAvatar", "deleteAvatar"]) {
  test(`stalled ${action} times out and can be retried`, async (t) => {
    member(t);
    t.mock.timers.enable({ apis: ["setTimeout"] });
    let calls = 0;
    t.mock.method(globalThis, "fetch", async (_, { signal }) => {
      if (++calls > 1) return Response.json({ user: { id: 7, avatar_url: null } });
      return new Promise((_, reject) => signal.addEventListener("abort", () => reject(signal.reason), { once: true }));
    });
    const file = new File(["photo"], "photo.png", { type: "image/png" });
    const rejected = assert.rejects(api[action](file), { name: "TimeoutError" });
    t.mock.timers.tick(30_001);
    await rejected;
    assert.deepEqual(await api[action](file), { user: { id: 7, avatar_url: null } });
  });
}
