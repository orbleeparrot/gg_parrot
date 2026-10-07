import { test } from "node:test";
import assert from "node:assert/strict";
import {
  FIELDLESS_ERROR_FIELDS, buildBundleRisk, buildLegs, buildMacro, defaultForm, evenWeights, macroToForm,
  splitWeights, validateDetailed, weightsAfterAdd, withExchangeDefaults, withTypeDefaults,
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

// ── W1. 거래소를 바꾸면 묶음 칸이 저절로 422 조합을 만들지 않는다 ──
test("거래소를 바꾸면 비중 · 레그 규칙 · 묶음 한도가 초기값으로 돌아간다", () => {
  const listed = [{ symbol: "KRW-BTC" }, { symbol: "KRW-ETH" }];
  const before = form({
    rule_type: "K", symbol: "BTCUSDT, ETHUSDT",
    leg_weights: "70, 30",
    leg_rules: { BTCUSDT: { ...defaultForm(), rule_type: "E" } },
    use_bundle_risk: true, bundle_max_positions: "1", bundle_max_exposure_pct: "60",
  });
  // K 는 국내에서 못 쓰므로 규칙이 A 로 내려간다 — 서버는 한도를 E~K 에서만 받으므로
  // 칸이 그대로 남으면 사용자가 아무것도 안 해도 422 조합이 만들어진다.
  const after = withExchangeDefaults(before, "upbit", listed);
  assert.equal(after.rule_type, "A");
  assert.equal(after.leg_weights, "");
  assert.deepEqual(after.leg_rules, {});
  assert.equal(after.leg_shape, "");
  assert.equal(after.use_bundle_risk, false);
  assert.equal(after.bundle_max_positions, "");
  assert.equal(after.bundle_max_exposure_pct, "");
  // 되돌린 폼에는 묶음 오류가 남지 않고(거래소를 바꾸면 자금 칸을 다시 받으므로 그 오류는 남는다)
  // 매크로도 묶음 키를 싣지 않는다.
  const err = validateDetailed(after);
  assert.ok(!err || !FIELDLESS_ERROR_FIELDS.includes(err.field), JSON.stringify(err));
  const macro = buildMacro(after);
  assert.ok(!("bundle_risk" in macro));
  assert.ok(!("legs" in macro));
});

test("같은 거래소를 다시 고르면 폼이 그대로다(묶음 칸도 안 지운다)", () => {
  const before = form({ leg_weights: "70, 30", use_bundle_risk: true, bundle_max_positions: "1" });
  assert.equal(withExchangeDefaults(before, "binance", []), before);
});

// ── W2. 묶음 한도는 진입 조건을 달 수 있는 규칙(E~K)에서만 ──
test("E~K 가 아닌 규칙에 묶음 한도를 걸면 검증이 잡는다", () => {
  // 서버가 422 로 거절하고 그 422 는 detail 이 리스트로 와서 영어 날 JSON 이 뜬다.
  expectError(validateDetailed(form({ rule_type: "A", use_bundle_risk: true, bundle_max_positions: "1" })),
    "use_bundle_risk", /묶음 한도는 .*쓸 수 없어요/);
  for (const rt of ["A", "B", "C", "D"]) {
    const err = validateDetailed(form({ rule_type: rt, use_bundle_risk: true, bundle_max_exposure_pct: "60" }));
    assert.ok(err, `규칙 ${rt} 에 한도를 걸면 막아야 한다`);
    assert.equal(err.field, "use_bundle_risk");
  }
  // H 는 세이프티오더 자금 규칙이 따로 있어 기본 폼으로는 다른 오류가 먼저 난다 — 한도 쪽만 본다.
  for (const rt of ["E", "F", "G", "I", "J", "K"]) {
    assert.equal(validateDetailed(form({ rule_type: rt, use_bundle_risk: true, bundle_max_exposure_pct: "60" })), null, `규칙 ${rt}`);
  }
  const h = validateDetailed(form({ rule_type: "H", use_bundle_risk: true, bundle_max_exposure_pct: "60" }));
  assert.ok(!h || h.field !== "use_bundle_risk", "H 는 한도를 쓸 수 있는 규칙이다");
});

test("레그마다 덮어쓴 규칙이 E~K 가 아니면 묶음 한도를 막는다", () => {
  const err = validateDetailed(form({
    use_bundle_risk: true, bundle_max_positions: "1",
    leg_rules: { ETHUSDT: { ...defaultForm(), rule_type: "A" } },
  }));
  expectError(err, "use_bundle_risk", /쓸 수 없어요/);
  // 문구는 RULE_TYPES 의 라벨을 쓴다 — 규칙 글자 하나만 적으면 사용자가 모른다.
  assert.match(err.message, /익절\/손절 후 재진입/);
});

test("지운 종목에 남은 레그 규칙은 묶음 한도를 막지 않는다", () => {
  assert.equal(validateDetailed(form({
    use_bundle_risk: true, bundle_max_positions: "1",
    leg_rules: { XRPUSDT: { ...defaultForm(), rule_type: "A" } },
  })), null);
});

// ── W3. 칸이 없는 오류 목록 ──
test("비중 · 묶음 한도 · 레그 규칙 오류는 '칸 없는 오류' 로 분류된다", () => {
  // 이 목록에 없으면 화면이 그 오류를 어디에도 띄우지 않는다(칸도 없고 바닥 경고도 건너뛴다).
  for (const broken of [
    form({ leg_weights: "0, 100, 0".split(",").slice(0, 2).join(",") }),
    form({ leg_weights: "50, 30" }),
    form({ use_bundle_risk: true }),
    form({ rule_type: "A", use_bundle_risk: true, bundle_max_positions: "1" }),
    // 묶음 규칙은 숏을 할 수 있고(J) 레그 규칙만 못 하는 경우.
    form({ rule_type: "J", position_side: "short", leg_rules: { BTCUSDT: { ...defaultForm(), rule_type: "E" } } }),
  ]) {
    const err = validateDetailed(broken);
    assert.ok(err, JSON.stringify(broken.leg_weights));
    assert.ok(FIELDLESS_ERROR_FIELDS.includes(err.field), `${err.field} 는 칸이 없다 — 목록에 있어야 바닥 경고가 보여 준다`);
  }
});

// ── W4. 비중 쪼개기는 자리를 지킨다 ──
test("splitWeights 는 빈 칸의 자리를 지킨다", () => {
  assert.deepEqual(splitWeights("50, , 33.34"), ["50", "", "33.34"]);
  assert.deepEqual(splitWeights("33., 33.33, 33.34"), ["33.", "33.33", "33.34"]);
  // 통째로 비었으면 '손대지 않음' — buildLegs 가 옛 symbols 모양을 지킨다.
  assert.deepEqual(splitWeights(""), []);
  assert.deepEqual(splitWeights("   "), []);
  assert.deepEqual(splitWeights(null), []);
  // 빈 칸만 남은 상태는 '지우는 중' 이고 검증이 막는다.
  assert.deepEqual(splitWeights(", "), ["", ""]);
});

test("비중 한 칸을 비우면 검증이 막는다 — 뒤 칸 숫자가 그 자리로 밀려오지 않는다", () => {
  const err = validateDetailed(form({ symbol: "BTCUSDT, ETHUSDT, SOLUSDT", leg_weights: "50, , 33.34" }));
  expectError(err, "leg_weights", /0보다 크고/);
  // 걸러내던 옛 코드에서는 개수가 맞아 떨어져 '밀려온 숫자' 로 통과했다.
  assert.equal(splitWeights("50, , 33.34").length, 3);
});

// ── W9. 숏 묶음에 숏을 못 쓰는 레그 규칙을 막는다 ──
test("묶음이 숏이면 숏을 못 쓰는 레그 규칙을 막는다", () => {
  // J(이동평균 크로스)는 숏을 할 수 있고 E(트레일링 스탑)는 못 한다 — 서버도 레그 쪽은 안 본다.
  const err = validateDetailed(form({
    rule_type: "J", position_side: "short",
    leg_rules: { ETHUSDT: { ...defaultForm(), rule_type: "E" } },
  }));
  expectError(err, "leg_rules", /숏을 지원하지 않아요/);
  assert.match(err.message, /ETH/);
  // 롱 묶음이면 막지 않는다.
  assert.equal(validateDetailed(form({
    rule_type: "J", position_side: "long",
    leg_rules: { ETHUSDT: { ...defaultForm(), rule_type: "E" } },
  })), null);
});

// ── W9. legs 였으면 legs 로 되돌린다(복제가 손실 없게) ──
test("균등 legs 묶음을 복제해도 legs 모양이 유지된다", () => {
  const macro = { ...buildMacro(form({ leg_weights: "50, 50" })) };
  const back = macroToForm(macro);
  assert.ok(!back.leg_weights, "균등이면 비중 입력은 켜지 않는다");
  assert.equal(back.leg_shape, "legs");
  const again = buildMacro(back);
  assert.deepEqual(again.legs, macro.legs, "legs 가 symbols 로 바뀌지 않는다");
  assert.equal(again.symbols, null);
});

test("근사균등 legs(49.99/50.01)를 복제해도 비중이 그대로다", () => {
  const macro = {
    ...buildMacro(form({ leg_weights: "50, 50" })),
    legs: [{ symbol: "BTCUSDT", weight: 49.99 }, { symbol: "ETHUSDT", weight: 50.01 }],
  };
  const back = macroToForm(macro);
  assert.deepEqual(buildMacro(back).legs, macro.legs);
});

test("symbols 묶음을 복제하면 symbols 그대로다 — 모양을 바꾸지 않는다", () => {
  const macro = buildMacro(form());
  const back = macroToForm(macro);
  assert.equal(back.leg_shape, "");
  assert.deepEqual(buildMacro(back), macro);
});

// ── W8. 종목을 더하면 남은 몫을 나눠 준다 ──
test("종목을 더하면 새 종목이 남은 몫을 받는다", () => {
  assert.equal(weightsAfterAdd("50, 20"), "50, 20, 30");
  assert.equal(weightsAfterAdd("40, 40"), "40, 40, 20");
  // 합이 100 으로 꽉 찼으면 남은 몫이 없으니 전체를 균등으로 다시 나눈다 — 합 125% 로 깨진 채 두지 않는다.
  assert.equal(weightsAfterAdd("70, 30"), "33.33, 33.33, 33.34");
  assert.equal(weightsAfterAdd("50, 50, 10"), "25, 25, 25, 25");
  // 손대지 않은 비중은 그대로 — 균등은 종목 수가 바뀌면 저절로 다시 나뉜다.
  assert.equal(weightsAfterAdd(""), "");
});

test("종목을 더한 뒤의 비중은 검증을 통과한다", () => {
  const next = weightsAfterAdd("50, 20");
  assert.equal(validateDetailed(form({ symbol: "BTCUSDT, ETHUSDT, SOLUSDT", leg_weights: next })), null);
  const even = weightsAfterAdd("70, 30");
  assert.equal(validateDetailed(form({ symbol: "BTCUSDT, ETHUSDT, SOLUSDT", leg_weights: even })), null);
});
