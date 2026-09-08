import assert from "node:assert/strict";
import test from "node:test";

import { applyVote, settleVote } from "../src/lib/leaderboardVotes.js";
import { appendMessage } from "../src/lib/chatFeed.js";

const entry = { id: 7, likes: 3, dislikes: 1, my_vote: 0 };

test("a fresh like shows up instantly and toggles off on the second press", () => {
  const liked = applyVote(entry, 1);
  assert.deepEqual([liked.likes, liked.dislikes, liked.my_vote], [4, 1, 1]);
  const undone = applyVote(liked, 1);
  assert.deepEqual([undone.likes, undone.dislikes, undone.my_vote], [3, 1, 0]);
});

test("switching from like to dislike moves the count instead of double counting", () => {
  const liked = applyVote(entry, 1);
  const switched = applyVote(liked, -1);
  assert.deepEqual([switched.likes, switched.dislikes, switched.my_vote], [3, 2, -1]);
});

test("the server tally overwrites only the voted entry", () => {
  const items = [entry, { id: 8, likes: 0, dislikes: 0, my_vote: 0 }];
  const settled = settleVote(items, { entry_id: 7, likes: 10, dislikes: 2, my_vote: 1 });
  assert.deepEqual(settled[0], { id: 7, likes: 10, dislikes: 2, my_vote: 1 });
  assert.equal(settled[1], items[1]);
  assert.equal(settleVote(items, null), items);
});

test("a sent chat message appears once even if polling already delivered it", () => {
  const items = [{ id: 1, text: "a" }];
  const appended = appendMessage(items, { id: 2, text: "b" });
  assert.equal(appended.length, 2);
  assert.equal(appendMessage(appended, { id: 2, text: "b" }).length, 2);
  assert.equal(appendMessage(items, null), items);
});
