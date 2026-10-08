import assert from "node:assert/strict";
import test from "node:test";
import { api } from "../src/api.js";
import { clearAuth, getToken, setAuth } from "../src/lib/auth.js";

function member(t, token = "member-token") {
  const values = new Map();
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
    removeItem: (key) => values.delete(key),
  } });
  setAuth(token, { id: 7, username: "회원" });
  t.after(() => {
    clearAuth();
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  });
}

const expired = () => Response.json({ detail: "세션이 만료됐거나 유효하지 않아요. 다시 로그인해 주세요." }, { status: 401 });

test("보낸 토큰이 401 을 받으면 로그인 상태를 지운다 — 헤더가 계속 로그인으로 남던 것", async (t) => {
  member(t);
  t.mock.method(globalThis, "fetch", async () => expired());
  await assert.rejects(api.myDashboard(), { status: 401, message: "세션이 만료됐거나 유효하지 않아요. 다시 로그인해 주세요." });
  assert.equal(getToken(), "");
});

test("탈퇴 확인에서 구글 재인증이 틀린 401 은 로그인 상태를 그대로 둔다", async (t) => {
  member(t);
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: "구글 인증에 실패했어요. 다시 시도해 주세요." }, { status: 401 }));
  await assert.rejects(api.deleteAccount({ confirmation: "탈퇴", credential: "bad" }), { status: 401 });
  assert.equal(getToken(), "member-token");
});

test("요청 중에 다른 계정으로 바뀌었으면 옛 토큰의 401 이 새 로그인을 지우지 않는다", async (t) => {
  member(t, "old-token");
  let respond;
  t.mock.method(globalThis, "fetch", () => new Promise((resolve) => { respond = resolve; }));
  const pending = api.myDashboard();
  await new Promise(setImmediate);
  setAuth("new-token", { id: 8, username: "다른 회원" });
  respond(expired());
  await assert.rejects(pending, { status: 401 });
  assert.equal(getToken(), "new-token");
});
