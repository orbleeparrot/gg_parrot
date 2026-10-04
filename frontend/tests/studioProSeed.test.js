import assert from "node:assert/strict";
import { test } from "node:test";
import { seedForm } from "../src/lib/studioProSeed.js";
import { RULE_TYPES, buildMacro, defaultForm } from "../src/lib/macro.js";

const DEFAULT = defaultForm();

test("넘어온 조건이 없으면 기본 조건으로 시작한다", () => {
  assert.deepEqual(seedForm(undefined), DEFAULT);
  assert.deepEqual(seedForm(null), DEFAULT);
  assert.deepEqual(seedForm({}), DEFAULT);
  assert.deepEqual(seedForm({ macro: null }), DEFAULT);
  assert.deepEqual(seedForm({ macro: "A" }), DEFAULT);
});

test("읽을 수 없는 매크로는 기본 조건으로 떨어진다(빈 객체 · 배열 · 모르는 매매 방식)", () => {
  for (const macro of [{}, [], { rule_type: "Z" }, { rule_type: undefined, symbol: "BTCUSDT" }, { exchange: "nowhere", rule_type: "A" }]) {
    const form = seedForm({ macro });
    assert.deepEqual(form, DEFAULT, JSON.stringify(macro));
    assert.ok(Object.hasOwn(RULE_TYPES, form.rule_type), "조건 판이 그릴 수 있는 매매 방식이어야 한다");
  }
});

test("멀쩡한 매크로는 그대로 이어받는다", () => {
  const source = { ...DEFAULT, exchange: "upbit", symbol: "KRW-ETH", candle_interval: "1h", initial_capital: 2000000 };
  const macro = buildMacro(source);
  const form = seedForm({ macro });
  assert.equal(form.exchange, "upbit");
  assert.equal(form.symbol, "KRW-ETH");
  assert.equal(form.candle_interval, "1h");
  assert.deepEqual(buildMacro(form), macro, "한 바퀴 돌려도 같다");
});
