/**
 * Read-only application audit; only client-cache-results.json is written.
 * Run with Node 24+ after the existing frontend dependencies are installed:
 *   node docs/cache-audit-2026-09-14/verify_client_cache.mjs
 *
 * No server, network, real account, browser storage, or temporary directory is
 * used. The assertions describe defects present at the audited baseline; they
 * should fail after those defects are fixed and are not regression criteria.
 */
import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";

function memoryStorage() {
  const values = new Map();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, String(value)),
    removeItem: (key) => values.delete(key),
  };
}

globalThis.localStorage = memoryStorage();
globalThis.sessionStorage = memoryStorage();
globalThis.fetch = () => { throw new Error("Network access is forbidden in this audit"); };

const { api } = await import("../../frontend/src/api.js");
const { loadSymbolList, resetSymbolList } = await import("../../frontend/src/hooks/useSymbolList.js");
const { createNewsCache } = await import("../../frontend/src/lib/newsBriefings.js");
const { mergeMessages, chatWindow } = await import("../../frontend/src/lib/chatFeed.js");
const { writeStudioSession, readStudioSession } = await import("../../frontend/src/lib/studioSession.js");
const { setAuth, clearAuth, updateAuthUser } = await import("../../frontend/src/lib/auth.js");

const actualDateNow = Date.now;
const realSymbols = api.symbols;
let simulatedNow = Date.UTC(2026, 8, 14);
Date.now = () => simulatedNow;
const findings = [];

try {
  // A cached successful-but-stale response never reaches its source again.
  let symbolRequests = 0;
  api.symbols = async () => ({
    items: [{ symbol: ++symbolRequests === 1 ? "OLDUSDT" : "NEWUSDT" }],
    stale: true,
  });
  resetSymbolList();
  await loadSymbolList();
  simulatedNow += 10 * 24 * 60 * 60 * 1000;
  const symbols = await loadSymbolList();
  assert.equal(symbolRequests, 1);
  assert.equal(symbols.items[0].symbol, "OLDUSDT");
  assert.equal(symbols.stale, true);
  findings.push({
    id: "symbol_cache_no_expiry",
    observed: { simulated_elapsed_days: 10, source_calls: symbolRequests, symbol: symbols.items[0].symbol, stale: symbols.stale },
    conclusion: "Advancing the clock by ten days does not expire the symbol cache.",
  });

  // Use the real API client's list cache and profile mutation with local
  // response fixtures. Update auth as ProfileEditor does after saving.
  let boardGets = 0;
  let sourceAuthor = "OLD";
  globalThis.fetch = async (path, options) => {
    let body;
    if (String(path).startsWith("/api/board/posts?") && options.method === "GET") {
      boardGets += 1;
      body = { items: [{ id: 1, author_user_id: 1, author_name: sourceAuthor }], total: 1 };
    } else if (path === "/api/me/profile" && options.method === "PATCH") {
      sourceAuthor = "NEW";
      body = { user: { id: 1, username: sourceAuthor, avatar_url: null } };
    } else {
      throw new Error(`Unexpected request; network is prohibited: ${path}`);
    }
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  };
  setAuth("fake-audit-token-A", { id: 1, username: "OLD", avatar_url: null });
  await api.boardList();
  const changed = await api.updateProfile({ username: "NEW", bio: "" });
  updateAuthUser(changed.user);
  const afterEdit = await api.boardList();
  assert.equal(boardGets, 1);
  assert.equal(afterEdit.items[0].author_name, "OLD");
  assert.equal(sourceAuthor, "NEW");
  findings.push({
    id: "profile_edit_missing_board_invalidation",
    observed: { board_gets: boardGets, source_author: sourceAuthor, returned_author: afterEdit.items[0].author_name },
    conclusion: "The board list returns its cached old author name after a successful profile edit.",
    caveat: "This is about author_name. AuthorAvatar separately overrides the signed-in user's photo from auth state.",
  });

  // Synthetic cardinality stress, not the observed size of a production cache.
  const newsStorage = memoryStorage();
  const news = createNewsCache({ storage: newsStorage, now: () => simulatedNow, maxAgeMs: 1 });
  for (let index = 0; index < 1000; index += 1) news.set(`coin-${index}`, { items: [] });
  simulatedNow += 2;
  news.set("fresh", { items: [] });
  const persisted = JSON.parse(newsStorage.getItem("ggp_news_cache_v1"));
  assert.equal(Object.keys(persisted).length, 1001);
  findings.push({
    id: "news_cache_no_global_eviction",
    synthetic_stress: true,
    observed: { expired_entries_inserted: 1000, fresh_entries_inserted: 1, persisted_entries: Object.keys(persisted).length },
    conclusion: "Writing a new cache entry retains unrelated expired entries in the persisted cache.",
    caveat: "Artificial 1,000-key stress with a 1 ms TTL; not a production memory, traffic, or latency measurement.",
  });

  // The render window does not impose a limit on the underlying message cache.
  const messages = mergeMessages([], Array.from({ length: 5000 }, (_, index) => ({
    id: index + 1, text: "synthetic audit message", created_ms: simulatedNow,
  })));
  const rendered = chatWindow(messages).items;
  assert.equal(messages.length, 5000);
  assert.equal(rendered.length, 200);
  findings.push({
    id: "chat_render_window_not_cache_limit",
    synthetic_stress: true,
    observed: { messages_supplied: 5000, messages_retained: messages.length, messages_in_render_window: rendered.length },
    conclusion: "The 200-message rendering window retains all 5,000 supplied messages in its backing array.",
    caveat: "Synthetic message count, not observed production traffic. Server day boundaries prune prior-day messages.",
  });

  const draft = { form: { symbol: "SYNTHETIC_PRIVATE_DRAFT" }, testedMacro: { id: "audit-draft" }, result: { return: 123 } };
  writeStudioSession(draft);
  clearAuth();
  setAuth("fake-audit-token-B", { id: 2, username: "OTHER" });
  const restored = readStudioSession();
  assert.deepEqual(restored, draft);
  findings.push({
    id: "studio_session_not_account_scoped",
    observed: { original_account_id: 1, new_account_id: 2, prior_draft_restored: true, retained_fields: Object.keys(restored) },
    conclusion: "Logging out and switching accounts leaves the previous Studio draft and result available for restoration.",
    caveat: "Same-browser local storage boundary only; this does not demonstrate bypassing server authorization.",
  });

  const results = {
    audited_baseline: "f30fbee",
    verification: "offline Node reproduction against application modules",
    all_five_baseline_behaviors_reproduced: findings.length === 5,
    external_requests: 0,
    real_accounts_used: 0,
    caveat: "Synthetic fixtures prove cache behavior, not production performance. Assertions intentionally describe the audited defects.",
    findings,
  };
  await writeFile(new URL("./client-cache-results.json", import.meta.url), JSON.stringify(results, null, 2) + "\n");
  process.stdout.write(JSON.stringify(results, null, 2) + "\n");
} finally {
  Date.now = actualDateNow;
  api.symbols = realSymbols;
  resetSymbolList();
}
