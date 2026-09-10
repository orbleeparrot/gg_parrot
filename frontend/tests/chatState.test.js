import assert from "node:assert/strict";
import test from "node:test";
import { chatScope, countUnseen, isOwnMessage, readSeenId, sameAuthor, seenStorageKey, writeSeenId } from "../src/lib/chatBadge.js";
import { mergeMessages } from "../src/lib/chatFeed.js";
import { chatUnseenCount, getChatFeed, markChatSeen, observeChat, receiveChat, receiveChatPost, visibleChatReadId } from "../src/lib/chatStore.js";

const values = new Map();
const storageListeners = [];
globalThis.window = {
  localStorage: { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value) },
  addEventListener: (event, listener) => { if (event === "storage") storageListeners.push(listener); },
};
const start = Date.parse("2026-09-08T00:00:00Z");
const msg = (id, user_id = 2, created_at = "2026-09-08T01:00:00Z") => ({ id, user_id, username: "same-name", text: `message ${id}`, created_at });
const response = (items, fields = {}) => ({ items, day_start_ms: start, latest_id: items.at(-1)?.id ?? 0, seen_id: 0, unseen_count: items.length, has_more: false, ...fields });

test("ownership uses member ID; same names and anonymous legacy rows never impersonate a member", () => {
  assert.equal(isOwnMessage(msg(1, 2), 1), false);
  assert.equal(isOwnMessage(msg(1, 1), "1"), true);
  assert.equal(isOwnMessage(msg(1, null), 1), false);
  assert.equal(sameAuthor(msg(1, null), msg(2, null)), false);
  assert.equal(sameAuthor(msg(1, 1), msg(2, 2)), false);
  assert.equal(sameAuthor(msg(1, 1), msg(2, 1)), true);
  assert.equal(countUnseen([msg(1, 1), msg(2, 2), msg(3, null)], 0, 1), 2);
});

test("account cursors exclude the legacy shared cursor and only advance", () => {
  values.set("ggparrot:chat-seen:v1", "999");
  assert.equal(readSeenId(chatScope("storage-a")), null);
  writeSeenId(8, chatScope("storage-a"));
  writeSeenId(3, chatScope("storage-a"));
  assert.equal(readSeenId(chatScope("storage-a")), 8);
  assert.equal(readSeenId(chatScope("storage-b")), null);
  assert.equal(readSeenId("anon"), null);
});

test("empty initial anonymous feed establishes zero so its first arrival is unread", () => {
  const scope = "test-empty";
  receiveChat(scope, response([], { seen_id: null }));
  assert.equal(getChatFeed(scope).seenId, 0);
  receiveChat(scope, response([msg(1)]));
  assert.equal(chatUnseenCount(getChatFeed(scope)), 1);
});

test("a feed cache preserves unread state between route subscriptions", () => {
  const scope = "test-remount";
  const unsubscribe = observeChat(scope, () => {});
  receiveChat(scope, response([msg(1), msg(2)], { seen_id: 1, unseen_count: 1 }));
  const cached = getChatFeed(scope);
  unsubscribe();
  const cleanup = observeChat(scope, () => {});
  assert.equal(getChatFeed(scope), cached);
  assert.equal(chatUnseenCount(getChatFeed(scope)), 1);
  cleanup();
});

test("polling replaces an author's photo without new messages or unread changes", () => {
  const scope = "test-avatar-refresh";
  receiveChat(scope, response([{ ...msg(1), avatar_url: "/api/auth/avatars/2?v=old" }], { seen_id: 1, unseen_count: 0 }));
  const cached = getChatFeed(scope);
  receiveChat(scope, response([{ ...msg(1), avatar_url: "/api/auth/avatars/2?v=new" }], { seen_id: 1, unseen_count: 0 }));
  assert.notEqual(getChatFeed(scope), cached);
  assert.equal(getChatFeed(scope).items[0].avatar_url, "/api/auth/avatars/2?v=new");
  assert.equal(getChatFeed(scope).latestId, 1);
  assert.equal(chatUnseenCount(getChatFeed(scope)), 0);
  receiveChat(scope, response([{ ...msg(1), avatar_url: null }], { seen_id: 1, unseen_count: 0 }));
  assert.equal(getChatFeed(scope).items[0].avatar_url, null);
});

test("other tabs advance the live badge immediately without moving a cursor backwards", () => {
  const scope = "test-tabs";
  const cleanup = observeChat(scope, () => {});
  receiveChat(scope, response([msg(1), msg(2)], { seen_id: 1, unseen_count: 1 }));
  values.set(seenStorageKey(scope), "2");
  storageListeners.forEach((listener) => listener({ key: seenStorageKey(scope), newValue: "2" }));
  assert.equal(chatUnseenCount(getChatFeed(scope)), 0);
  values.set(seenStorageKey(scope), "1");
  storageListeners.forEach((listener) => listener({ key: seenStorageKey(scope), newValue: "1" }));
  assert.equal(getChatFeed(scope).seenId, 2);
  assert.equal(readSeenId(scope), 2);
  cleanup();
});

test("a stale GET cannot remove a confirmed POST or increase unread after reading", () => {
  const scope = "test-post-race";
  receiveChat(scope, response([msg(1)], { seen_id: 1, unseen_count: 0 }));
  receiveChatPost(scope, msg(2, 1));
  receiveChat(scope, response([msg(1)], { seen_id: 0, unseen_count: 1 }));
  assert.deepEqual(getChatFeed(scope).items.map((item) => item.id), [1, 2]);
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 0);
  markChatSeen(scope, 2);
  receiveChat(scope, response([msg(1), msg(2)], { seen_id: 0, unseen_count: 2 }));
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 0);
});

test("server counts include unread rows outside the loaded 200; own messages are excluded", () => {
  const scope = "test-full-count";
  const items = Array.from({ length: 200 }, (_, index) => msg(index + 151));
  receiveChat(scope, response(items, { unseen_count: 350, has_more: true }));
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 350);
  receiveChatPost(scope, msg(351, 1));
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 350);
  markChatSeen(scope, 351);
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 0);
});

test("history pagination remains available after regular latest-page polling", () => {
  const scope = "test-history";
  receiveChat(scope, response([msg(3), msg(4)], { has_more: true }));
  receiveChat(scope, response([msg(1), msg(2)], { latest_id: 4, has_more: false }), { older: true });
  receiveChat(scope, response([msg(3), msg(4)], { has_more: true }));
  assert.deepEqual(getChatFeed(scope).items.map((item) => item.id), [1, 2, 3, 4]);
  assert.equal(getChatFeed(scope).hasMore, false);
});

test("midnight prunes yesterday while preserving a new-day POST ahead of its GET", () => {
  const scope = "test-midnight";
  receiveChat(scope, response([msg(1)]));
  receiveChatPost(scope, msg(2, 1, "2026-09-09T01:00:00Z"));
  const nextDay = start + 86400000;
  receiveChat(scope, response([], { day_start_ms: nextDay, latest_id: 0, unseen_count: 0 }));
  assert.deepEqual(getChatFeed(scope).items.map((item) => item.id), [2]);
  receiveChat(scope, response([msg(1)]));
  assert.deepEqual(getChatFeed(scope).items.map((item) => item.id), [2]);
  assert.deepEqual(mergeMessages([msg(1)], [msg(2, 1, "2026-09-09T01:00:00Z")], nextDay).map((item) => item.id), [2]);
});

test("late history counts never restore an older read baseline, even with newer arrivals", () => {
  const scope = "test-stale-history-count";
  const items = Array.from({ length: 200 }, (_, index) => msg(index + 201));
  receiveChat(scope, response(items, { seen_id: 150, unseen_count: 250, has_more: true }));
  receiveChat(scope, response(items, { seen_id: 0, unseen_count: 400, has_more: true }));
  assert.equal(getChatFeed(scope).seenId, 150);
  assert.equal(getChatFeed(scope).unreadSeenId, 150);
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 250);
  receiveChat(scope, response([...items, msg(401)], { seen_id: 0, unseen_count: 401, has_more: true }));
  assert.equal(getChatFeed(scope).unreadSeenId, 150);
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 251);
  assert.equal(getChatFeed(scope).countNeedsRefresh, true);
  receiveChat(scope, response([...items, msg(401)], { seen_id: 150, unseen_count: 251, has_more: true }));
  assert.equal(getChatFeed(scope).countNeedsRefresh, false);
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 251);
});

test("returning after over 200 arrivals resets disconnected history and can fill the gap", () => {
  const scope = "test-history-gap";
  receiveChat(scope, response([msg(4), msg(5)], { has_more: true }));
  receiveChat(scope, response([msg(1), msg(2), msg(3)], { latest_id: 5 }), { older: true, beforeId: 4 });
  assert.equal(getChatFeed(scope).hasMore, false);
  // A confirmed recent POST must not disguise the missing messages 6–205.
  receiveChatPost(scope, msg(405, 1));
  receiveChatPost(scope, msg(406, 1));
  const newest = Array.from({ length: 200 }, (_, index) => msg(index + 206));
  receiveChat(scope, response(newest, { has_more: true, unseen_count: 405 }));
  assert.equal(getChatFeed(scope).oldestId, 206);
  assert.equal(getChatFeed(scope).hasMore, true);
  assert.equal(getChatFeed(scope).historyLoaded, false);
  assert.equal(getChatFeed(scope).items.at(-1).id, 406);
  // An older query already in flight before the reset cannot close pagination.
  const cached = getChatFeed(scope);
  receiveChat(scope, response([], { latest_id: 405 }), { older: true, beforeId: 1 });
  assert.equal(getChatFeed(scope), cached);
  receiveChat(scope, response([msg(1), msg(2), msg(3), msg(4), msg(5)]));
  assert.equal(getChatFeed(scope), cached, "a stale latest page cannot reopen the disconnected range");
  const missing = Array.from({ length: 200 }, (_, index) => msg(index + 6));
  receiveChat(scope, response(missing, { latest_id: 405, has_more: true, unseen_count: 405 }), { older: true, beforeId: 206 });
  assert.equal(getChatFeed(scope).oldestId, 6);
  assert.equal(getChatFeed(scope).items.length, 401);
  receiveChat(scope, response([msg(1), msg(2), msg(3), msg(4), msg(5)], { latest_id: 405, unseen_count: 405 }), { older: true, beforeId: 6 });
  assert.equal(getChatFeed(scope).items.length, 406);
  assert.equal(getChatFeed(scope).hasMore, false);
});


test("older-page metadata cannot mark newer messages that have not been fetched", () => {
  const scope = "test-history-read-ceiling";
  const latestPage = Array.from({ length: 100 }, (_, index) => msg(index + 201));
  receiveChat(scope, response(latestPage, { seen_id: 200, unseen_count: 100, has_more: true }));
  const olderPage = Array.from({ length: 200 }, (_, index) => msg(index + 1));
  receiveChat(scope, response(olderPage, { latest_id: 350, seen_id: 200, unseen_count: 150 }), { older: true, beforeId: 201 });
  assert.equal(getChatFeed(scope).countNeedsRefresh, true);
  assert.equal(visibleChatReadId(getChatFeed(scope)), 300);
  markChatSeen(scope, visibleChatReadId(getChatFeed(scope)));
  assert.equal(getChatFeed(scope).seenId, 300);
  assert.equal(chatUnseenCount(getChatFeed(scope)), 50);
});

test("a POST ahead of polling cannot read intervening messages that are absent", () => {
  const scope = "test-post-read-ceiling";
  receiveChat(scope, response([msg(1)], { seen_id: 1, unseen_count: 0 }));
  // Another member sent 2, but only our POST response 3 has reached this tab.
  receiveChatPost(scope, msg(3, 1));
  assert.equal(visibleChatReadId(getChatFeed(scope)), 1);
  markChatSeen(scope, visibleChatReadId(getChatFeed(scope)));
  assert.equal(getChatFeed(scope).seenId, 1);
  receiveChat(scope, response([msg(1), msg(2), msg(3, 1)], { seen_id: 1, unseen_count: 1 }));
  assert.equal(chatUnseenCount(getChatFeed(scope), 1), 1);
  assert.equal(visibleChatReadId(getChatFeed(scope)), 3);
});
