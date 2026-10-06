import { test } from "node:test";
import assert from "node:assert/strict";
import {
  buildBundleRisk, buildLegs, buildMacro, defaultForm, evenWeights, macroToForm, validateDetailed, withTypeDefaults,
} from "../src/lib/macro.js";

// 돌파(I) 규칙 + 종목 셋. defaultForm 위에 얹어야 buildMacro 가 제대로 돈다.
function form(over = {}) {
  return { ...defaultForm(), rule_type: "I", symbol: "BTCUSDT, ETHUSDT", ...over };
}

test("evenWeights 는 100 을 균등하게 쪼개고 합이 정확히 100 이다", () => {
  assert.deepEqual(evenWeights(2), [50, 50]);
  assert.deepEqual(evenWeights(4), [25, 25, 25, 25]);
  const three = evenWeights(3);
  assert.equal(three.length, 3);
  assert.equal(three.reduce((a, b) => a + b, 0), 100);
});

test("비중을 안 건드리면 legs 를 만들지 않는다 (옛 symbols 모양 유지)", () => {
  assert.equal(buildLegs(form()), null);
});

test("비중을 건드리면 legs 를 만든다", () => {
  assert.deepEqual(buildLegs(form({ leg_weights: "70, 30" })), [
    { symbol: "BTCUSDT", weight: 70 },
    { symbol: "ETHUSDT", weight: 30 },
  ]);
});

test("레그 규칙을 바꾸면 rule_type 과 params 가 함께 들어간다", () => {
  const legs = buildLegs(form({
    leg_weights: "50, 50",
    leg_rules: { BTCUSDT: { ...defaultForm(), rule_type: "E", trail_percent: 3 } },
  }));
  assert.equal(legs[0].rule_type, "E");
  assert.equal(legs[0].params.trail_percent, 3);
  assert.equal(legs[1].rule_type, undefined);
});

test("묶음 한도는 체크를 켰을 때만 만들어진다", () => {
  assert.equal(buildBundleRisk(form()), null);
  assert.equal(buildBundleRisk(form({ use_bundle_risk: true })), null, "둘 다 비면 null");
  assert.deepEqual(
    buildBundleRisk(form({ use_bundle_risk: true, bundle_max_positions: "1" })),
    { max_positions: 1 },
  );
  assert.deepEqual(
    buildBundleRisk(form({ use_bundle_risk: true, bundle_max_exposure_pct: "60" })),
    { max_exposure_pct: 60 },
  );
});

test("buildMacro 가 legs 와 bundle_risk 를 싣고 symbols 를 비운다", () => {
  const macro = buildMacro(form({
    leg_weights: "70, 30", use_bundle_risk: true, bundle_max_positions: "1",
  }));
  assert.equal(macro.legs.length, 2);
  assert.equal(macro.bundle_risk.max_positions, 1);
  assert.equal(macro.symbols, null, "legs 를 쓰면 symbols 를 같이 보내지 않는다");
  assert.equal(macro.symbol, "BTCUSDT", "대표 종목은 첫 레그");
});

test("비중을 안 건드리면 매크로가 전과 같다 (legs 없음)", () => {
  const macro = buildMacro(form());
  assert.deepEqual(macro.symbols, ["BTCUSDT", "ETHUSDT"]);
  assert.ok(!macro.legs);
  assert.ok(!macro.bundle_risk);
  // 키 목록과 순서까지 옛 모양 그대로 — 서명이 바뀌지 않는다.
  assert.deepEqual(Object.keys(macro), [
    "exchange", "quote_currency", "symbol", "symbols", "rule_type", "position_side", "candle_interval",
    "leverage", "margin_mode", "market", "params", "entry_filter", "risk", "period", "fees",
  ]);
});

test("macroToForm 왕복", () => {
  const macro = buildMacro(form({
    leg_weights: "70, 30", use_bundle_risk: true, bundle_max_exposure_pct: "60",
  }));
  const back = macroToForm(macro);
  assert.equal(back.symbol, "BTCUSDT, ETHUSDT");
  assert.equal(back.leg_weights, "70, 30");
  assert.equal(back.use_bundle_risk, true);
  assert.equal(Number(back.bundle_max_exposure_pct), 60);
});

test("macroToForm 은 비중이 균등하면 leg_weights 를 비워 둔다", () => {
  const back = macroToForm(buildMacro(form({ leg_weights: "50, 50" })));
  assert.ok(!back.leg_weights, "균등이면 비중 입력을 안 켠다");
  assert.equal(back.symbol, "BTCUSDT, ETHUSDT");
});

test("macroToForm 이 레그 규칙을 되살린다", () => {
  const macro = buildMacro(form({
    leg_weights: "50, 50",
    leg_rules: { BTCUSDT: { ...defaultForm(), rule_type: "E", trail_percent: 3 } },
  }));
  const back = macroToForm(macro);
  assert.equal(back.leg_rules.BTCUSDT.rule_type, "E");
  assert.equal(Number(back.leg_rules.BTCUSDT.trail_percent), 3);
  assert.ok(!back.leg_rules.ETHUSDT, "규칙을 안 바꾼 레그는 항목이 없다");
});

// validateDetailed 는 오류 배열이 아니라 첫 오류 하나 `{ field, message }` 를 돌려주고, 없으면 null 이다.
function expectError(err, field, pattern) {
  assert.ok(err, "오류가 나와야 한다");
  assert.equal(err.field, field, JSON.stringify(err));
  assert.match(err.message, pattern);
}

test("기본 묶음 폼은 검증을 통과한다", () => {
  assert.equal(validateDetailed(form()), null);
  assert.equal(validateDetailed(form({ leg_weights: "70, 30", use_bundle_risk: true, bundle_max_positions: "1", bundle_max_exposure_pct: "60" })), null);
});

test("비중 합이 100 이 아니면 검증이 잡는다", () => {
  expectError(validateDetailed(form({ leg_weights: "70, 20" })), "leg_weights", /비중/);
});

test("비중 개수가 종목 수와 다르면 검증이 잡는다", () => {
  expectError(validateDetailed(form({ leg_weights: "50, 30, 20" })), "leg_weights", /비중/);
});

test("종목이 하나면 비중을 정할 수 없다", () => {
  expectError(validateDetailed(form({ symbol: "BTCUSDT", leg_weights: "100" })), "leg_weights", /2개 이상/);
});

test("동시 보유 상한은 정수여야 하고 종목 수보다 작아야 한다", () => {
  expectError(validateDetailed(form({ use_bundle_risk: true, bundle_max_positions: "1.5" })), "bundle_max_positions", /동시 보유/);
  expectError(validateDetailed(form({ use_bundle_risk: true, bundle_max_positions: "2" })), "bundle_max_positions", /종목 수/);
});

test("한도는 종목 2개 이상에서만", () => {
  expectError(validateDetailed(form({
    symbol: "BTCUSDT", use_bundle_risk: true, bundle_max_positions: "1",
  })), "use_bundle_risk", /2개 이상/);
});

test("총 노출 한도 범위", () => {
  expectError(validateDetailed(form({ use_bundle_risk: true, bundle_max_exposure_pct: "0" })), "bundle_max_exposure_pct", /노출/);
  expectError(validateDetailed(form({ use_bundle_risk: true, bundle_max_exposure_pct: "101" })), "bundle_max_exposure_pct", /노출/);
});

test("한도를 켰는데 둘 다 비면 검증이 잡는다", () => {
  expectError(validateDetailed(form({ use_bundle_risk: true })), "use_bundle_risk", /한도/);
});

test("한도를 끈 채 값이 남아 있으면 검증도 매크로도 무시한다", () => {
  const f = form({ use_bundle_risk: false, bundle_max_positions: "9" });
  assert.equal(validateDetailed(f), null);
  assert.ok(!("bundle_risk" in buildMacro(f)));
});

test("단일 종목은 한도가 남아 있어도 bundle_risk 를 싣지 않는다", () => {
  const macro = buildMacro(form({ symbol: "BTCUSDT", use_bundle_risk: true, bundle_max_positions: "1" }));
  assert.ok(!("bundle_risk" in macro));
  assert.ok(!("legs" in macro));
});

test("비중 없이 한도만 켜면 symbols 를 유지하고 bundle_risk 만 싣는다", () => {
  const macro = buildMacro(form({ use_bundle_risk: true, bundle_max_positions: "1" }));
  assert.deepEqual(macro.symbols, ["BTCUSDT", "ETHUSDT"]);
  assert.deepEqual(macro.bundle_risk, { max_positions: 1 });
  assert.ok(!("legs" in macro));
});

test("레그 규칙에 부분만 채워도 params 가 NaN 이 되지 않는다", () => {
  const legs = buildLegs(form({ leg_rules: { ETHUSDT: { rule_type: "E" } } }));
  assert.equal(legs[1].rule_type, "E");
  assert.ok(Object.values(legs[1].params).every((v) => typeof v !== "number" || Number.isFinite(v)), JSON.stringify(legs[1].params));
  assert.deepEqual(legs.map((l) => l.weight), [50, 50]);
});

test("지운 종목의 레그 규칙은 무시한다", () => {
  assert.equal(buildLegs(form({ leg_rules: { XRPUSDT: { ...defaultForm(), rule_type: "E" } } })), null);
});

test("레그 규칙에 진입 필터를 켜면 레그에 entry_filter 가 들어간다", () => {
  const legs = buildLegs(form({
    leg_weights: "60, 40",
    leg_rules: { BTCUSDT: { ...defaultForm(), rule_type: "E", use_entry_filter: true, filter_kind: "rsi", filter_rsi_max: 70 } },
  }));
  assert.equal(legs[0].entry_filter.kind, "rsi");
  assert.equal(legs[1].entry_filter, undefined);
});

test("withTypeDefaults 는 레그 규칙을 건드리지 않는다", () => {
  const leg_rules = { BTCUSDT: { ...defaultForm(), rule_type: "E" } };
  assert.deepEqual(withTypeDefaults(form({ leg_rules }), "J").leg_rules, leg_rules);
});

test("레그 규칙까지 든 매크로가 왕복해도 같은 매크로가 나온다", () => {
  const macro = buildMacro(form({
    leg_weights: "40, 60", use_bundle_risk: true, bundle_max_positions: "1",
    leg_rules: { ETHUSDT: { ...defaultForm(), rule_type: "E", trail_percent: 4 } },
  }));
  assert.deepEqual(buildMacro(macroToForm(macro)), macro);
});
