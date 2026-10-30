// 구동 중 설정 변경 — 입력란의 글자를 서버가 받는 모양으로 바꾸는 규칙 (2026-10-30).
//
// 가장 조심할 것은 **빈 칸의 뜻**이다. 끌 수 있는 칸(손절)을 비우면 '끄는 것' 이고,
// 끌 수 없는 칸을 비운 것은 '입력을 덜 한 것' 이다. 둘을 같게 다루면 사용자가 칸을
// 지웠다가 투자 비율이 사라지거나, 손절을 끄려 했는데 그대로 남는다.
import assert from "node:assert/strict";
import test from "node:test";
import {
  badFields, buildChanges, changeSummary, fieldText, hasChanges, parseNumber, showValue,
} from "../src/features/agents/settingsForm.js";

const money = { path: "params.initial_capital", section: "params", key: "initial_capital",
                label: "투자 금액", value: 1000000, unit: "money", clearable: false };
const stop = { path: "risk.stop_loss_pct", section: "risk", key: "stop_loss_pct",
               label: "손절", value: 3, unit: "pct", clearable: true };
const ratio = { path: "risk.invest_ratio", section: "risk", key: "invest_ratio",
                label: "투자 비율", value: 1, unit: "ratio", clearable: false };
const period = { path: "params.rsi_period", section: "params", key: "rsi_period",
                 label: "RSI 기간", value: 14, unit: "", clearable: false };
const FIELDS = [money, stop, ratio, period];

test("parseNumber: takes what people actually type", () => {
  assert.equal(parseNumber("1,000,000"), 1000000);
  assert.equal(parseNumber(" 3.5 "), 3.5);
  assert.equal(parseNumber(""), null);
  assert.equal(parseNumber("많이"), null);
  assert.equal(parseNumber("1e5"), 100000);
});

test("buildChanges: only the fields that actually changed", () => {
  const changes = buildChanges(FIELDS, {
    "params.initial_capital": "2,000,000",
    "risk.stop_loss_pct": "3",       // 그대로
    "params.rsi_period": "21",
  });
  assert.deepEqual(changes, { params: { initial_capital: 2000000, rsi_period: 21 } });
});

test("buildChanges: a field nobody touched is never sent", () => {
  assert.deepEqual(buildChanges(FIELDS, {}), {});
});

test("buildChanges: clearing a clearable field turns it off", () => {
  assert.deepEqual(buildChanges(FIELDS, { "risk.stop_loss_pct": "" }),
    { risk: { stop_loss_pct: null } });
});

test("buildChanges: clearing a field that was already off changes nothing", () => {
  const off = { ...stop, value: null };
  assert.deepEqual(buildChanges([off], { "risk.stop_loss_pct": "" }), {});
});

test("buildChanges: emptying a field that cannot be cleared is ignored, not sent as null", () => {
  // 투자 비율을 지운 것은 '0 으로 해 달라' 가 아니라 '아직 덜 쳤다' 다.
  assert.deepEqual(buildChanges(FIELDS, { "risk.invest_ratio": "" }), {});
});

test("buildChanges: nonsense text is never sent as a number", () => {
  assert.deepEqual(buildChanges(FIELDS, { "params.rsi_period": "많이" }), {});
});

test("badFields: says which boxes cannot be read as a number", () => {
  const bad = badFields(FIELDS, { "params.rsi_period": "많이", "risk.stop_loss_pct": "" });
  assert.deepEqual(bad.map((f) => f.path), ["params.rsi_period"]);
});

test("badFields: an emptied non-clearable field is flagged, an emptied clearable one is not", () => {
  assert.deepEqual(badFields(FIELDS, { "risk.invest_ratio": "" }).map((f) => f.path),
    ["risk.invest_ratio"]);
  assert.deepEqual(badFields(FIELDS, { "risk.stop_loss_pct": "" }), []);
});

test("hasChanges: tells the button whether there is anything to send", () => {
  assert.equal(hasChanges(FIELDS, {}), false);
  assert.equal(hasChanges(FIELDS, { "params.rsi_period": "14" }), false);
  assert.equal(hasChanges(FIELDS, { "params.rsi_period": "21" }), true);
});

test("fieldText: money reads with separators, an empty setting reads empty", () => {
  assert.equal(fieldText(money), "1,000,000");
  assert.equal(fieldText(period), "14");
  assert.equal(fieldText({ ...stop, value: null }), "");
});

test("showValue: each unit reads the way people say it", () => {
  assert.equal(showValue(money, 2000000), "2,000,000");
  assert.equal(showValue(stop, 1.5), "1.5%");
  assert.equal(showValue(ratio, 0.5), "50%");
  assert.equal(showValue(stop, null), "없음");
  assert.equal(showValue({ unit: "hours" }, 12), "12시간");
});

test("changeSummary: the confirm box reads both sides, in the field order", () => {
  const text = changeSummary(FIELDS, {
    "params.initial_capital": "2000000", "risk.stop_loss_pct": "",
  });
  assert.equal(text, "투자 금액 1,000,000 → 2,000,000 · 손절 3% → 없음");
});

test("changeSummary: nothing changed reads empty", () => {
  assert.equal(changeSummary(FIELDS, { "params.rsi_period": "14" }), "");
});
