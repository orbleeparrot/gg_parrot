import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/api.js";
import { clearAuth, getAuthUser, mergeFetchedAuthUser, setAuth, updateAuthUser } from "../src/lib/auth.js";

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

test("profile edits send text and photo together; password and withdrawal secrets stay in request bodies", async (t) => {
  member(t);
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, options) => { calls.push({ url, ...options }); return Response.json({ user: { id: 7 } }); });
  const file = new File(["photo"], "profile.png", { type: "image/png" });
  await api.updateProfile({ username: "new_name", bio: "소개", image: file });
  assert.equal(calls[0].method, "PATCH");
  assert.equal(calls[0].body.get("username"), "new_name");
  assert.equal(calls[0].body.get("bio"), "소개");
  assert.equal(calls[0].body.get("image").name, "profile.png");
  await api.changePassword({ currentPassword: "old-secret", newPassword: "new-secret" });
  assert.deepEqual(JSON.parse(calls[1].body), { current_password: "old-secret", new_password: "new-secret" });
  await api.deleteAccount({ confirmation: "탈퇴", password: "old-secret", credential: "" });
  assert.equal(calls[2].method, "DELETE");
  assert.equal(JSON.parse(calls[2].body).confirmation, "탈퇴");
  assert.ok(calls.every((call) => !call.url.includes("secret") && call.headers.Authorization === "Bearer avatar-member-token"));
  assert.equal(JSON.stringify(getAuthUser()).includes("secret"), false);
});

test("late account reads preserve edited profile fields while accepting fresh points", (t) => {
  member(t);
  const before = { id: 7, username: "before", bio: "old", avatar_url: null, points_balance: 10 };
  updateAuthUser({ ...before, username: "after", bio: "new", avatar_url: "/new-photo" });
  assert.deepEqual(mergeFetchedAuthUser({ ...before, points_balance: 20 }, before), {
    ...before, username: "after", bio: "new", avatar_url: "/new-photo", points_balance: 20,
  });
  updateAuthUser({ ...before, points_balance: 30 });
  assert.equal(mergeFetchedAuthUser({ ...before, avatar_url: "/server-photo" }, before).avatar_url, "/server-photo");
  assert.deepEqual(mergeFetchedAuthUser({ id: 8, username: "another" }, before), { id: 8, username: "another" });
});
