// 직접 만들기 — 마틴게일 한 건이 거래소 최소 주문 금액보다 작으면 칸에 알리고 [맞추기] 값을 낸다(2026-10-17 업비트 4,700원).
import assert from "node:assert/strict";
import test from "node:test";
import { defaultForm, fitMartingale, validateDetailed, withExchangeDefaults, withTypeDefaults } from "../src/lib/macro.js";

function upbitH(over = {}) {
  const form = withTypeDefaults(withExchangeDefaults(defaultForm(), "upbit", []), "H");
  return { ...form, symbol: "KRW-GTC", initial_capital: 47000, base_order_size: 4700, safety_order_size: 4700,
           max_safety_orders: 5, safety_order_volume_scale: 1, price_deviation: 2, take_profit: 2, ...over };
}

test("4,700원이면 첫 주문 칸에 알리고, 맞추기는 5,100원 · 추가 5번", () => {
  const err = validateDetailed(upbitH());
  assert.equal(err.field, "base_order_size");
  assert.equal(err.message, "업비트 최소 주문 금액은 5,000원이에요. 이대로면 첫 주문이 거절돼요.");
  assert.deepEqual(err.fix, { base_order_size: 5100, safety_order_size: 5100, max_safety_orders: 5, label: "1회 5,100원 · 추가 5번으로 맞추기" });
  const fixed = upbitH(err.fix);
  assert.equal(validateDetailed(fixed), null, "맞춘 값은 검사를 통과한다");
});

test("예산을 넘으면 횟수를 줄이고, 두 번도 못 넣으면 시작 자금을 올리라고 한다", () => {
  assert.equal(fitMartingale(upbitH({ initial_capital: 20000 })).max_safety_orders, 2);
  const err = validateDetailed(upbitH({ initial_capital: 9000 }));
  assert.equal(err.fix, undefined);
  assert.match(err.message, /시작 자금을 10,200원 이상으로 올리세요\.$/);
});

test("추가매수만 작아도 그 칸에 알리고, 이미 넘으면 아무 말 없다", () => {
  const err = validateDetailed(upbitH({ base_order_size: 6000, safety_order_size: 3000 }));
  assert.equal(err.field, "safety_order_size");
  assert.equal(err.message, "업비트 최소 주문 금액은 5,000원이에요. 이대로면 추가매수 주문이 거절돼요.");
  assert.equal(err.fix.label, "처음 10,200원 · 추가 5,100원씩 5번으로 맞추기");
  assert.equal(validateDetailed(upbitH({ base_order_size: 5100, safety_order_size: 5100 })), null);
});
