import assert from "node:assert/strict";
import { test } from "node:test";
import { FILTERABLE_RULE_TYPES, FILTER_KINDS, buildMacro, defaultForm, macroToForm, validateDetailed, withTypeDefaults }
  from "../src/lib/macro.js";

const breakout = () => withTypeDefaults({ ...defaultForm(), symbol: "BTCUSDT" }, "I");
const on = (extra) => ({ ...breakout(), use_entry_filter: true, ...extra });

test("필터를 끄면 매크로에 entry_filter 가 없다", () => {
  assert.equal(buildMacro(breakout()).entry_filter, null);
});

test("이동평균 필터를 켜면 매크로에 실린다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20, filter_ma_side: "above" };
  assert.deepEqual(buildMacro(form).entry_filter, { kind: "ma", params: { ma_type: "SMA", period: 20, side: "above" } });
});

test("RSI 필터는 비워 둔 쪽을 보내지 않는다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: "", filter_rsi_max: 70 };
  const params = buildMacro(form).entry_filter.params;
  assert.deepEqual(params, { period: 14, max: 70 });
  assert.equal("min" in params, false);
});

test("RSI 필터는 위 한도를 비워도 아래 한도만 보낸다", () => {
  const form = on({ filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: 30, filter_rsi_max: "" });
  const params = buildMacro(form).entry_filter.params;
  assert.deepEqual(params, { period: 14, min: 30 });
  assert.equal("max" in params, false);
});

test("RSI 필터는 0 도 값으로 보낸다", () => {
  const form = on({ filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: 0, filter_rsi_max: "" });
  assert.deepEqual(buildMacro(form).entry_filter.params, { period: 14, min: 0 });
});

test("거래량 필터가 실린다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "volume", filter_vol_period: 20, filter_vol_multiple: 2 };
  assert.deepEqual(buildMacro(form).entry_filter.params, { period: 20, multiple: 2 });
});

test("볼린저 필터가 실린다", () => {
  const form = on({ filter_kind: "bb", filter_bb_period: 20, filter_bb_num_std: 2.5, filter_bb_zone: "below_lower" });
  assert.deepEqual(buildMacro(form).entry_filter, { kind: "bb", params: { period: 20, num_std: 2.5, zone: "below_lower" } });
});

test("폼 값이 문자열이어도 숫자로 보낸다", () => {
  const form = on({ filter_kind: "ma", filter_ma_period: "50", filter_ma_side: "below", filter_ma_type: "EMA" });
  assert.deepEqual(buildMacro(form).entry_filter, { kind: "ma", params: { ma_type: "EMA", period: 50, side: "below" } });
});

test("필터를 못 쓰는 규칙으로 바꾸면 필터를 버린다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20, filter_ma_side: "above" };
  for (const rt of ["A", "B", "C", "D"]) {
    assert.equal(withTypeDefaults(form, rt).use_entry_filter, false, rt);
    assert.equal(buildMacro(withTypeDefaults(form, rt)).entry_filter, null, rt);
  }
});

test("켜 둔 채 못 쓰는 규칙이 된 폼도 매크로에는 필터를 싣지 않는다", () => {
  const stale = { ...breakout(), use_entry_filter: true, filter_kind: "ma", rule_type: "A" };
  assert.equal(buildMacro(stale).entry_filter, null);
  assert.equal(validateDetailed({ ...stale, filter_ma_period: 1 })?.field?.startsWith("filter_") ?? false, false);
});

test("필터를 쓰는 규칙끼리 바꾸면 필터가 남는다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 20, filter_ma_side: "above" };
  for (const rt of FILTERABLE_RULE_TYPES) {
    assert.equal(withTypeDefaults(form, rt).use_entry_filter, true, rt);
  }
});

test("일곱 규칙만 필터를 쓴다", () => {
  assert.deepEqual([...FILTERABLE_RULE_TYPES].sort(), ["E", "F", "G", "H", "I", "J", "K"]);
});

test("필터 종류는 서버의 네 종류와 같다", () => {
  assert.deepEqual(FILTER_KINDS.map((k) => k.value), ["ma", "rsi", "bb", "volume"]);
});

test("검증이 서버와 같은 규칙으로 먼저 막는다", () => {
  const bad = { ...breakout(), use_entry_filter: true, filter_kind: "ma", filter_ma_period: 1, filter_ma_side: "above" };
  assert.equal(validateDetailed(bad).field, "filter_ma_period");
  const noBound = { ...breakout(), use_entry_filter: true, filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: "", filter_rsi_max: "" };
  assert.ok(validateDetailed(noBound).field.startsWith("filter_rsi"));
  const crossed = { ...breakout(), use_entry_filter: true, filter_kind: "rsi", filter_rsi_period: 14, filter_rsi_min: 70, filter_rsi_max: 30 };
  assert.ok(validateDetailed(crossed));
});

test("검증은 서버가 받는 경계값을 통과시키고 한 칸 벗어나면 막는다", () => {
  const field = (extra) => validateDetailed(on(extra))?.field ?? null;
  // 이동평균 기간 2~400
  assert.equal(field({ filter_kind: "ma", filter_ma_period: 2 }), null);
  assert.equal(field({ filter_kind: "ma", filter_ma_period: 400 }), null);
  assert.equal(field({ filter_kind: "ma", filter_ma_period: 1 }), "filter_ma_period");
  assert.equal(field({ filter_kind: "ma", filter_ma_period: 401 }), "filter_ma_period");
  // RSI 기간 2~200, 한도 0~100
  assert.equal(field({ filter_kind: "rsi", filter_rsi_period: 2 }), null);
  assert.equal(field({ filter_kind: "rsi", filter_rsi_period: 200 }), null);
  assert.equal(field({ filter_kind: "rsi", filter_rsi_period: 1 }), "filter_rsi_period");
  assert.equal(field({ filter_kind: "rsi", filter_rsi_period: 201 }), "filter_rsi_period");
  assert.equal(field({ filter_kind: "rsi", filter_rsi_min: 0, filter_rsi_max: 100 }), null);
  assert.equal(field({ filter_kind: "rsi", filter_rsi_min: -1, filter_rsi_max: 70 }), "filter_rsi_min");
  assert.equal(field({ filter_kind: "rsi", filter_rsi_min: "", filter_rsi_max: 101 }), "filter_rsi_max");
  assert.equal(field({ filter_kind: "rsi", filter_rsi_min: 50, filter_rsi_max: 50 }), null);
  assert.equal(field({ filter_kind: "rsi", filter_rsi_min: 51, filter_rsi_max: 50 }), "filter_rsi_min");
  // 볼린저 기간 2~400, 배수 0 초과 5 이하
  assert.equal(field({ filter_kind: "bb", filter_bb_period: 2, filter_bb_num_std: 5 }), null);
  assert.equal(field({ filter_kind: "bb", filter_bb_period: 400, filter_bb_num_std: 0.1 }), null);
  assert.equal(field({ filter_kind: "bb", filter_bb_period: 1 }), "filter_bb_period");
  assert.equal(field({ filter_kind: "bb", filter_bb_period: 401 }), "filter_bb_period");
  assert.equal(field({ filter_kind: "bb", filter_bb_num_std: 0 }), "filter_bb_num_std");
  assert.equal(field({ filter_kind: "bb", filter_bb_num_std: 5.1 }), "filter_bb_num_std");
  // 거래량 기간 2~400, 배수 0 초과 100 이하
  assert.equal(field({ filter_kind: "volume", filter_vol_period: 2, filter_vol_multiple: 100 }), null);
  assert.equal(field({ filter_kind: "volume", filter_vol_period: 400, filter_vol_multiple: 0.5 }), null);
  assert.equal(field({ filter_kind: "volume", filter_vol_period: 1 }), "filter_vol_period");
  assert.equal(field({ filter_kind: "volume", filter_vol_period: 401 }), "filter_vol_period");
  assert.equal(field({ filter_kind: "volume", filter_vol_multiple: 0 }), "filter_vol_multiple");
  assert.equal(field({ filter_kind: "volume", filter_vol_multiple: 100.5 }), "filter_vol_multiple");
});

test("필터를 끄면 세부 값이 틀려도 검증이 막지 않는다", () => {
  const off = { ...breakout(), use_entry_filter: false, filter_kind: "ma", filter_ma_period: 1 };
  assert.equal(validateDetailed(off), null);
});

test("켠 필터가 왕복한다", () => {
  const form = { ...breakout(), use_entry_filter: true, filter_kind: "bb", filter_bb_period: 20, filter_bb_num_std: 2, filter_bb_zone: "inside" };
  const back = macroToForm(buildMacro(form));
  assert.equal(back.use_entry_filter, true);
  assert.equal(back.filter_kind, "bb");
  assert.equal(back.filter_bb_zone, "inside");
});

test("네 종류 모두 매크로로 만들었다가 폼으로 되돌려도 같은 매크로가 나온다", () => {
  for (const extra of [
    { filter_kind: "ma", filter_ma_type: "EMA", filter_ma_period: 50, filter_ma_side: "below" },
    { filter_kind: "rsi", filter_rsi_period: 9, filter_rsi_min: 30, filter_rsi_max: "" },
    { filter_kind: "bb", filter_bb_period: 25, filter_bb_num_std: 1.5, filter_bb_zone: "above_upper" },
    { filter_kind: "volume", filter_vol_period: 30, filter_vol_multiple: 3 },
  ]) {
    const macro = buildMacro(on(extra));
    assert.deepEqual(buildMacro(macroToForm(macro)).entry_filter, macro.entry_filter, extra.filter_kind);
  }
});

test("필터 없는 옛 매크로를 읽으면 꺼진 상태다", () => {
  const macro = buildMacro(breakout());
  delete macro.entry_filter;
  assert.equal(macroToForm(macro).use_entry_filter, false);
});

test("entry_filter 가 null 인 매크로를 읽어도 꺼진 상태다", () => {
  assert.equal(macroToForm(buildMacro(breakout())).use_entry_filter, false);
});
