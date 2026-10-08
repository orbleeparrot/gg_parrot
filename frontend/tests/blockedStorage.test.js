import assert from "node:assert/strict";
import test from "node:test";
import { clearAuth, getToken, setAuth } from "../src/lib/auth.js";
import { getUserId, setNickname, getNickname } from "../src/lib/user.js";

// 사이트 데이터를 막은 브라우저 — localStorage 에 손대기만 해도 SecurityError 가 난다.
function blockedStorage(t) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() { throw new DOMException("The operation is insecure.", "SecurityError"); },
  });
  t.after(() => {
    clearAuth();
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else delete globalThis.localStorage;
  });
}

test("저장소가 막혀도 로그인은 이 탭 동안 유지되고 로그아웃도 예외 없이 된다", (t) => {
  blockedStorage(t);
  assert.doesNotThrow(() => setAuth("tab-only-token", { id: 3, username: "회원" }));
  assert.equal(getToken(), "tab-only-token");
  assert.doesNotThrow(() => clearAuth());
  assert.equal(getToken(), "");
});

test("저장소가 막혀도 익명 아이디는 한 탭 안에서 같은 값이다(리더보드가 깨지지 않는다)", (t) => {
  blockedStorage(t);
  const first = getUserId();
  assert.match(first, /^u_/);
  assert.equal(getUserId(), first);
  assert.doesNotThrow(() => setNickname("껄무새"));
  assert.equal(getNickname(), "");
});
