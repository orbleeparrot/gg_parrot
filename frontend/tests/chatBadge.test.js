import assert from "node:assert/strict";
import test from "node:test";

import { badgeLabel, countUnseen, latestMessageId } from "../src/lib/chatBadge.js";

const items = [{ id: 11 }, { id: 12 }, { id: 15 }];

test("unseen count only includes messages newer than the last seen id", () => {
  assert.equal(latestMessageId(items), 15);
  assert.equal(countUnseen(items, 12), 1);
  assert.equal(countUnseen(items, 15), 0);
  assert.equal(countUnseen(items, 0), 3);
  assert.equal(countUnseen(items, null), 0, "저장된 값이 없으면 배지를 띄우지 않는다");
  assert.equal(countUnseen([], 3), 0);
});

test("the badge reads as a count plus 'new' and caps at 99+", () => {
  assert.equal(badgeLabel(0), "");
  assert.equal(badgeLabel(3), "3 new");
  assert.equal(badgeLabel(140), "99+ new");
});
