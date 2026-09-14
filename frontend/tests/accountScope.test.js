import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { captureAccountGuard, clearAuth, getAuthScope, getAuthUser, setAuth, updateAuthUser } from "../src/lib/auth.js";
import { accountStorageKey } from "../src/lib/accountStorage.js";
import { readStudioSession, studioPaperKey, writeStudioSession } from "../src/lib/studioSession.js";
import { peekRegistrationDraft, saveRegistrationDraft, takeRegistrationDraft, readHeroDraft, saveHeroDraft } from "../src/lib/journey.js";

function memoryStorage() {
  const values = new Map();
  return {
    get length() { return values.size; }, key: (i) => [...values.keys()][i] ?? null,
    getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key), clear: () => values.clear(),
  };
}
function storage(t) {
  globalThis.localStorage = memoryStorage();
  globalThis.sessionStorage = memoryStorage();
  clearAuth();
  t.after(() => { clearAuth(); delete globalThis.localStorage; delete globalThis.sessionStorage; });
}
const user = (id) => ({ id, username: `member_${id}`, points_balance: id === 2 ? 900 : 100 });
const macro = { symbol: "BTCUSDT", rule_type: "A", params: {}, position_side: "long", candle_interval: "1m" };

test("guest work continues at login, then logout removes owned drafts and paper resumes", (t) => {
  storage(t);
  writeStudioSession({ form: { symbol: "BTCUSDT" } });
  saveHeroDraft(macro);
  saveRegistrationDraft(macro);
  sessionStorage.setItem(studioPaperKey(), JSON.stringify({ session: { session_id: "guest-paper" } }));
  setAuth("token-a", user(1));
  assert.equal(getAuthScope(), "member:1");
  assert.equal(readStudioSession().form.symbol, "BTCUSDT");
  assert.deepEqual(readHeroDraft(), macro);
  assert.deepEqual(peekRegistrationDraft().macro, macro);
  assert.equal(JSON.parse(sessionStorage.getItem(studioPaperKey())).session.session_id, "guest-paper");
  assert.equal(sessionStorage.getItem(accountStorageKey("studio", "guest")), null);
  clearAuth();
  assert.equal(readStudioSession(), null);
  assert.equal(readHeroDraft(), null);
  assert.equal(peekRegistrationDraft(), null);
  assert.equal(sessionStorage.length, 0);
});

test("a different account cannot read or resurrect the previous account's pending save", (t) => {
  storage(t);
  setAuth("token-a", user(1));
  const oldScope = getAuthScope();
  writeStudioSession({ form: { symbol: "PRIVATEUSDT" } });
  setAuth("token-b", user(2));
  assert.equal(readStudioSession(), null);
  assert.equal(readStudioSession(oldScope), null);
  writeStudioSession({ form: { symbol: "LATEUSDT" } }, oldScope);
  assert.equal(readStudioSession(), null);
  assert.equal(sessionStorage.getItem(accountStorageKey("studio", oldScope)), null);
});

test("same-account token renewal and profile edits preserve studio/paper work", (t) => {
  storage(t);
  setAuth("token-a", user(1));
  writeStudioSession({ form: { symbol: "ETHUSDT" }, explanation: { private: true } });
  sessionStorage.setItem(studioPaperKey(), "resume");
  const guard = captureAccountGuard();
  const sameMember = captureAccountGuard({ accountOnly: true });
  updateAuthUser({ ...user(1), username: "renamed" });
  assert.equal(guard(), true);
  setAuth("token-a-new-version", user(1));
  assert.equal(readStudioSession().form.symbol, "ETHUSDT");
  assert.equal(sessionStorage.getItem(studioPaperKey()), "resume");
  assert.equal(guard(), false, "An old credential result is distinguishable from current requests");
  assert.equal(sameMember(), true, "Owned work remains valid after a same-member credential renewal");
});

test("a reauthentication draft is readable only after the same member signs in", (t) => {
  storage(t);
  setAuth("token-a", user(1));
  saveRegistrationDraft(macro);
  writeStudioSession({ private: "discard on expired login" });
  clearAuth({ preserveRegistrationDraft: true });
  assert.equal(peekRegistrationDraft(), null);
  assert.equal(readStudioSession(), null);
  setAuth("token-a-new", user(1));
  assert.deepEqual(takeRegistrationDraft().macro, macro);
  assert.equal(peekRegistrationDraft(), null);
});

test("reauthenticating as another member discards the outgoing member draft", (t) => {
  storage(t);
  setAuth("token-a", user(1));
  saveRegistrationDraft(macro);
  clearAuth({ preserveRegistrationDraft: true });
  setAuth("token-b", user(2));
  assert.equal(peekRegistrationDraft(), null);
  assert.equal(sessionStorage.getItem(accountStorageKey("registration", "member:1")), null);
});

test("legacy unscoped work is never assigned to the next guest or member", (t) => {
  storage(t);
  for (const key of ["ggp_studio:v1", "ggp_studio_paper:v1", "ggp_registration_draft", "ggp_hero_draft:v1"]) {
    sessionStorage.setItem(key, JSON.stringify({ private: "unknown owner" }));
  }
  assert.equal(readStudioSession(), null);
  assert.equal(peekRegistrationDraft(), null);
  assert.equal(sessionStorage.length, 0);
});

test("old account guards stay retired after A -> B -> A, including reused tokens", (t) => {
  storage(t);
  setAuth("token-a", user(1));
  const current = captureAccountGuard();
  setAuth("token-b", user(2));
  setAuth("token-a", user(1));
  assert.equal(current(), false);
});

test("late unlock responses cannot change another account's points or navigate it", async (t) => {
  storage(t);
  const source = readFileSync(new URL("../src/pages/Leaderboard.jsx", import.meta.url), "utf8");
  const start = source.indexOf("async function unlock(entry)");
  const end = source.indexOf("\n  async function useForQuickRun", start);
  assert.ok(start >= 0 && end > start);
  let finish, navigations = 0, reloads = 0;
  setAuth("token-a", user(1));
  const current = captureAccountGuard();
  const unlock = new Function(
    "api", "isLoggedIn", "getAuthUser", "updateAuthUser", "navigate", "quickRunMode", "setError", "setUnlocking", "load", "isCurrentAccount",
    `return (${source.slice(start, end).trim()});`,
  )(
    { leaderboardUnlock: () => new Promise(resolve => { finish = resolve; }) },
    () => true, getAuthUser, updateAuthUser, () => { navigations++; }, true, () => {}, () => {},
    async () => { reloads++; }, current,
  );
  const pending = unlock({ id: 11 });
  setAuth("token-b", user(2));
  finish({ points_balance: 80, user_macro: { id: 99 } });
  await pending;
  assert.equal(getAuthUser().id, 2);
  assert.equal(getAuthUser().points_balance, 900);
  assert.equal(navigations, 0);
  assert.equal(reloads, 0);
});
