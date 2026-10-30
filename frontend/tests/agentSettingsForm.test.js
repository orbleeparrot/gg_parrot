// 구동 중 설정 변경 — 입력란의 글자를 서버가 받는 모양으로 바꾸는 규칙 (2026-10-30).
//
// 가장 조심할 것은 **빈 칸의 뜻**이다. 끌 수 있는 칸(손절)을 비우면 '끄는 것' 이고,
// 끌 수 없는 칸을 비운 것은 '입력을 덜 한 것' 이다. 둘을 같게 다루면 사용자가 칸을
// 지웠다가 투자 비율이 사라지거나, 손절을 끄려 했는데 그대로 남는다.
import assert from "node:assert/strict";
import test from "node:test";
import {
  badFields, buildChanges, changeSummary, fieldText, hasChanges, parseNumber, showValue,
  touchesPosition,
} from "../src/features/agents/settingsForm.js";

// 칸 모양은 서버(app/runner_settings.py form_view)가 내려 주는 그대로다 — unit_text 와
// affects 도 서버가 정한다. 여기서 지어내면 화면과 서버가 두 벌이 된다.
const money = { path: "params.initial_capital", section: "params", key: "initial_capital",
                label: "투자 금액", value: 1000000, unit: "money", unit_text: "USDT",
                affects: "next_entry", clearable: false };
const stop = { path: "risk.stop_loss_pct", section: "risk", key: "stop_loss_pct",
               label: "손절", value: 3, unit: "pct", unit_text: "%",
               affects: "position", clearable: true };
const ratio = { path: "risk.invest_ratio", section: "risk", key: "invest_ratio",
                label: "투자 비율", value: 1, unit: "ratio", unit_text: "",
                affects: "next_entry", clearable: false };
const period = { path: "params.rsi_period", section: "params", key: "rsi_period",
                 label: "RSI 기간", value: 14, unit: "", unit_text: "봉",
                 affects: "position", clearable: false };
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
  assert.equal(showValue(money, 2000000), "2,000,000 USDT");
  assert.equal(showValue(stop, 1.5), "1.5%");
  assert.equal(showValue(ratio, 0.5), "50%", "비율만 저장 모양과 읽는 모양이 다르다");
  assert.equal(showValue(stop, null), "없음");
  assert.equal(showValue(period, 20), "20봉");
  assert.equal(showValue({ unit: "hours", unit_text: "시간" }, 12), "12시간");
});

test("showValue: 단위 글자가 없으면 숫자만 — 없는 단위를 지어내지 않는다", () => {
  const threshold = { unit: "", unit_text: "" };
  assert.equal(showValue(threshold, 30), "30");
});

test("showValue: 돈은 거래소가 정한 통화로 읽는다", () => {
  const won = { ...money, unit_text: "KRW" };
  assert.equal(showValue(won, 60000), "60,000 KRW");
});

test("changeSummary: the confirm box reads both sides, in the field order", () => {
  const text = changeSummary(FIELDS, {
    "params.initial_capital": "2000000", "risk.stop_loss_pct": "",
  });
  assert.equal(text, "투자 금액 1,000,000 USDT → 2,000,000 USDT · 손절 3% → 없음");
});

test("changeSummary: nothing changed reads empty", () => {
  assert.equal(changeSummary(FIELDS, { "params.rsi_period": "14" }), "");
});


// --- 바꾼 값이 지금 들고 있는 포지션을 건드리는가 ---------------------------

test("touchesPosition: 손절을 바꾸면 들고 있는 포지션의 청산 조건이 바뀐다", () => {
  assert.equal(touchesPosition(FIELDS, { "risk.stop_loss_pct": "2" }), true);
});

test("touchesPosition: 금액·비율만 바꾸면 지금 포지션과 상관없다", () => {
  assert.equal(touchesPosition(FIELDS, { "params.initial_capital": "2000000" }), false);
  assert.equal(touchesPosition(FIELDS, { "risk.invest_ratio": "0.5" }), false);
});

test("touchesPosition: 손 안 댄 칸은 세지 않는다 — 열어 두기만 해도 묻게 되면 안 된다", () => {
  assert.equal(touchesPosition(FIELDS, {}), false);
  assert.equal(touchesPosition(FIELDS, { "risk.stop_loss_pct": "3" }), false, "같은 값은 변경이 아니다");
});

test("touchesPosition: 둘을 같이 바꾸면 묻는다", () => {
  assert.equal(
    touchesPosition(FIELDS, { "params.initial_capital": "2000000", "params.rsi_period": "20" }),
    true,
  );
});
