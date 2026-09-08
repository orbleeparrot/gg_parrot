import test from "node:test";
import assert from "node:assert/strict";
import { layoutTreemap, racerWeight } from "../src/lib/treemap.js";

const near = (a, b, eps = 1e-9) => Math.abs(a - b) < eps;

test("tiles fill the whole rectangle with areas proportional to weight", () => {
  const items = [6, 6, 4, 3, 2, 2, 1].map((weight, i) => ({ id: i, weight }));
  const rects = layoutTreemap(items, 600, 400);
  assert.equal(rects.length, items.length);
  const total = rects.reduce((acc, r) => acc + r.width * r.height, 0);
  assert.ok(near(total, 1, 1e-6), `total area ${total}`);
  const first = rects.find((r) => r.item.id === 0);
  const last = rects.find((r) => r.item.id === 6);
  assert.ok(near((first.width * first.height) / (last.width * last.height), 6, 1e-6));
  for (const r of rects) {
    assert.ok(r.x >= -1e-9 && r.y >= -1e-9 && r.x + r.width <= 1 + 1e-9 && r.y + r.height <= 1 + 1e-9);
  }
});

test("the heaviest item lands top-left and tiles do not overlap", () => {
  const items = [40, 20, 15, 10, 8, 4, 3].map((weight, i) => ({ id: i, weight }));
  const rects = layoutTreemap(items, 800, 450);
  const first = rects.find((r) => r.item.id === 0);
  assert.ok(near(first.x, 0) && near(first.y, 0));
  for (let i = 0; i < rects.length; i += 1) {
    for (let j = i + 1; j < rects.length; j += 1) {
      const a = rects[i];
      const b = rects[j];
      const overlapX = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
      const overlapY = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
      assert.ok(overlapX <= 1e-9 || overlapY <= 1e-9, `tiles ${i} and ${j} overlap`);
    }
  }
});

test("weight grows with change (square root) and zero or negative change still gets a small tile", () => {
  assert.ok(near(racerWeight(16), 4));
  assert.ok(near(racerWeight(115) / racerWeight(4), Math.sqrt(115 / 4)));
  assert.ok(near(racerWeight(0), Math.sqrt(0.5)));
  assert.ok(near(racerWeight(-3), Math.sqrt(0.5)));
  assert.ok(near(racerWeight("x"), Math.sqrt(0.5)));
  assert.deepEqual(layoutTreemap([], 100, 100), []);
});
