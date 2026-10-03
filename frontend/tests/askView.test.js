import assert from "node:assert/strict";
import test from "node:test";
import {
  conditionLines, holdDiff, holdOf, marketLabel, numberParts, resultsHeadline, signedPoints, signedPct,
  sparkPaths, splitRuleLabel, whyHeadline, whyPoints,
} from "../src/lib/askView.js";
import { FEW_RESULTS_TEXT, NO_RESULTS_TEXT, TOP_RESULTS_TEXT } from "../src/lib/askCopy.js";

// 물어볼까 재설계(2026-09-30) — 해설 문장은 서버 engine/explain.py 의 틀 그대로 넣어 본다.
test("whyPoints: pulls the key figure and a label out of each server sentence", () => {
  const pts = whyPoints([
    "이 전략은 +226.5%인데 그냥 QNT 들고 있었으면 +277.7%였어 — 홀딩에 51.2%p 뒤졌어.",
    "가는 길에 최대 -7.5%까지 빠졌어 (MDD) — 감당할 만한 수준.",
    "총 41번 매매해서 승률 63%.",
    "최대 6번 연속으로 손절났어 — 실제였다면 심리적으로 버티기 힘든 구간이야.",
  ]);
  assert.equal(pts.length, 3, "최대 3줄");
  assert.deepEqual(pts.map((p) => [p.fig, p.label]), [["-51.2%p", "홀딩 대비"], ["-7.5%", "최대 낙폭"], ["63%", "승률"]]);
  assert.equal(pts[0].text, "이 전략은 +226.5%인데 그냥 QNT 들고 있었으면 +277.7%였어 — 홀딩에 51.2%p 뒤졌어.", "문장은 줄이지 않는다");
});

test("whyPoints: ahead of hold is positive; other templates and unknown sentences still get a figure", () => {
  const [ahead, fin, streak, liq, sharpe, other] = whyPoints([
    "이 전략은 +12.0%, 그냥 BTC 들고 있었으면 +5.0% — 홀딩보다 7.0%p 앞섰어.",
    "이 기간 최종 수익률은 -3.2%야.",
    "최대 6번 연속으로 손절났어 — 실제였다면 심리적으로 버티기 힘든 구간이야.",
    "레버리지 3배 때문에 기간 중 2번 청산돼 증거금을 통째로 날렸어.",
    "샤프지수 1.42 — 변동성 대비 수익이 준수했어.",
    "거래가 18회라 수수료 부담이 작아.",
  ], 6);
  assert.deepEqual([ahead.fig, ahead.label], ["+7.0%p", "홀딩 대비"]);
  assert.deepEqual([fin.fig, fin.label], ["-3.2%", "수익률"]);
  assert.deepEqual([streak.fig, streak.label], ["6번", "연속 손절"]);
  assert.deepEqual([liq.fig, liq.label], ["2번", "청산"]);
  assert.deepEqual([sharpe.fig, sharpe.label], ["1.42", "샤프지수"]);
  assert.deepEqual([other.fig, other.label], ["18회", ""]);
  assert.deepEqual(whyPoints(null), []);
});

test("whyHeadline drops the leading parrot emoji only", () => {
  assert.equal(whyHeadline("🦜 벌긴 벌었는데… 그냥 들고 있는 게 나았어"), "벌긴 벌었는데… 그냥 들고 있는 게 나았어");
  assert.equal(whyHeadline("플러스로 마감했어"), "플러스로 마감했어");
});

test("numberParts splits numbers for bold typesetting without losing text", () => {
  const parts = numberParts("최근 30일 중 21일이 하루 4% 넘게, k=0.6 · -3%");
  assert.equal(parts.map((p) => p.v).join(""), "최근 30일 중 21일이 하루 4% 넘게, k=0.6 · -3%");
  assert.deepEqual(parts.filter((p) => p.t === "num").map((p) => p.v), ["30", "21", "4%", "0.6", "-3%"]);
});

test("holdDiff / holdOf / signed formats", () => {
  const item = { metrics: { final_return_pct: 226.48 }, hold_return_pct: 277.7 };
  assert.equal(signedPoints(holdDiff(item)), "-51.2%p");
  assert.equal(holdDiff({ metrics: { final_return_pct: 3 }, hold_return_pct: null }), null);
  assert.equal(signedPoints(null), "—");
  assert.equal(holdOf([{ hold_return_pct: null }, { hold_return_pct: 12.5 }]), 12.5);
  assert.equal(holdOf([]), null);
  assert.equal(signedPct(9.8), "+9.80%");
  assert.equal(signedPct(-6.3), "-6.30%");
});

test("resultsHeadline follows the result count", () => {
  assert.equal(resultsHeadline(0), NO_RESULTS_TEXT);
  assert.equal(resultsHeadline(2), FEW_RESULTS_TEXT);
  assert.equal(resultsHeadline(3), TOP_RESULTS_TEXT);
});

test("conditionLines / marketLabel / splitRuleLabel", () => {
  const macro = { rule_type: "I", params: { k: 0.6, exit_mode: "trailing" }, candle_interval: "1h", market: "spot", leverage: 1 };
  assert.deepEqual(conditionLines(macro), ["변동성 돌파 k=0.6", "청산: trailing", "1h 봉"]);
  assert.equal(marketLabel(macro), "현물 · 1배");
  assert.equal(marketLabel({ market: "futures", leverage: 2, margin_mode: "isolated" }), "선물 · 격리 2배");
  assert.deepEqual(splitRuleLabel("I · 변동성 돌파 (래리 윌리엄스)"), { name: "I · 변동성 돌파", by: "래리 윌리엄스" });
  assert.deepEqual(splitRuleLabel("A · 익절/손절 후 재진입"), { name: "A · 익절/손절 후 재진입", by: "" });
});

test("sparkPaths draws inside the box with a break-even baseline", () => {
  const p = sparkPaths([1000, 990, 1040, 1100], 1000);
  assert.ok(p.line.startsWith("M0.0 ") && p.line.includes("L100.0 "));
  assert.equal(p.up, true);
  assert.match(p.base, /^M0 [\d.]+ H100$/);
  assert.equal(sparkPaths([1000], 1000), null);
  assert.equal(sparkPaths(null, 1000), null);
});

test("absolute order conditions display the selected exchange quote", () => {
  assert.deepEqual(conditionLines({ exchange: "binance", rule_type: "C", params: { interval_days: 7, amount_per_buy: 10 } }), ["7일마다 10 USDT씩 매수"]);
  assert.deepEqual(conditionLines({ exchange: "upbit", rule_type: "C", params: { interval_days: 7, amount_per_buy: 10000 } }), ["7일마다 10,000 KRW씩 매수"]);
  assert.match(conditionLines({ exchange: "bithumb", rule_type: "H", params: { base_order_size: 50000, max_safety_orders: 3, take_profit: 2 } })[0], /50,000 KRW/);
});
