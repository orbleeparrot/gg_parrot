import assert from "node:assert/strict";
import test from "node:test";
import { extraOffer } from "../src/lib/askExtra.js";

// 포인트로 횟수 추가 (2026-09-22): 무료를 다 쓴 뒤에만, 포인트가 충분할 때만, 하루 상한 안에서만 살 수 있다.
test("extraOffer: offered only when free quota is gone, with points and cap left", () => {
  const base = { remaining_today: 0, daily_limit: 5, extra_price: 30, extra_left_today: 5, points_balance: 100 };
  assert.deepEqual(extraOffer(base), { show: true, canBuy: true, label: "30P로 1번 더 물어보기", note: "보유 100P · 오늘 5번 더 살 수 있어요" });
  assert.equal(extraOffer({ ...base, remaining_today: 2 }).show, false);
  const poor = extraOffer({ ...base, points_balance: 29 });
  assert.equal(poor.show, true); assert.equal(poor.canBuy, false); assert.match(poor.note, /부족/);
  const capped = extraOffer({ ...base, extra_left_today: 0 });
  assert.equal(capped.show, true); assert.equal(capped.canBuy, false); assert.match(capped.note, /내일/);
  assert.equal(extraOffer(null).show, false);
  assert.equal(extraOffer({ error: true }).show, false);
});
