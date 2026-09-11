import test from "node:test";
import assert from "node:assert/strict";
import { defaultForm, validate, validateDetailed } from "../src/lib/macro.js";

test("기본 A 폼은 통과한다", () => {
  assert.equal(validateDetailed(defaultForm()), null);
  assert.equal(validate(defaultForm()), null);
});

test("H 기본값은 추가매수 금액이 자금을 넘어 '최대 추가매수 횟수' 칸을 가리킨다", () => {
  const form = { ...defaultForm(), rule_type: "H" };
  const error = validateDetailed(form);
  assert.equal(error.field, "max_safety_orders");
  assert.match(error.message, /최대 추가매수 금액/);
  assert.equal(validate(form), error.message);
});

test("숏 A 는 손절이 없으면 '손절 기준' 칸", () => {
  const form = { ...defaultForm(), rule_type: "A", position_side: "short", use_stop_loss: false };
  assert.equal(validateDetailed(form).field, "stop_loss_pct");
});

test("D 는 상단이 하단보다 작으면 '상단 가격' 칸", () => {
  const form = { ...defaultForm(), rule_type: "D", lower_price: 70000, upper_price: 50000 };
  assert.equal(validateDetailed(form).field, "upper_price");
});

test("C 에 레버리지를 걸면 '레버리지' 칸", () => {
  const form = { ...defaultForm(), rule_type: "C", leverage: 3 };
  assert.equal(validateDetailed(form).field, "leverage");
});

test("K 는 방어 시작 하락폭이 비면 그 칸", () => {
  const form = { ...defaultForm(), rule_type: "K", drop_trigger_pct: 0 };
  assert.equal(validateDetailed(form).field, "drop_trigger_pct");
});
