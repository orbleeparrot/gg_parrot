// Read-only audit reproduction. Runs the actual unlock handler with mock APIs.
// No real account, API request or browser storage is used.
// Run: node docs/cache-audit-2026-09-14/verify_unlock_account_cache.mjs
import assert from "node:assert/strict";
import { readFileSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { setAuth, getAuthUser, updateAuthUser } from "../../frontend/src/lib/auth.js";

const storage = new Map();
globalThis.localStorage = {
  getItem: (key) => storage.get(key) || null,
  setItem: (key, value) => storage.set(key, value),
  removeItem: (key) => storage.delete(key),
};
globalThis.fetch = async () => { throw Error("Network forbidden in cache audit"); };

const source = readFileSync(new URL("../../frontend/src/pages/Leaderboard.jsx", import.meta.url), "utf8");
const start = source.indexOf("async function unlock(entry)");
const end = source.indexOf("\n  async function useForQuickRun", start);
assert.ok(start >= 0 && end > start, "Unlock handler boundary changed; update this audit harness");
let finish;
const unlock = new Function(
  "api", "isLoggedIn", "getAuthUser", "updateAuthUser", "navigate", "quickRunMode",
  "setError", "setUnlocking", "load", `return (${source.slice(start, end).trim()});`,
)(
  { leaderboardUnlock: () => new Promise((resolve) => { finish = resolve; }) },
  () => true, getAuthUser, updateAuthUser, () => {}, false, () => {}, () => {}, async () => {},
);

setAuth("audit-account-a", { id: 1, username: "A", points_balance: 100 });
const pending = unlock({ id: 11 });
setAuth("audit-account-b", { id: 2, username: "B", points_balance: 900 });
finish({ points_balance: 80 });
await pending;

const observed = getAuthUser();
assert.equal(observed.id, 2);
assert.equal(observed.points_balance, 80, "Audited defect no longer reproduces; review the new behavior");
const report = {
  audit: "late-unlock-response-writes-current-account-cache",
  scope: "In-memory localStorage and mocked API; no server balance changes",
  source: "frontend/src/pages/Leaderboard.jsx",
  source_sha256: createHash("sha256").update(source).digest("hex"),
  steps: ["A starts unlock with 100 points", "Switch to B with 900 points", "A response returns 80 points"],
  expected_current_account: { id: 2, points_balance: 900 },
  observed_current_account: observed,
  defect_reproduced: true,
};
writeFileSync(new URL("unlock-account-cache-result.json", import.meta.url), JSON.stringify(report, null, 2) + "\n");
console.log(JSON.stringify(report));
