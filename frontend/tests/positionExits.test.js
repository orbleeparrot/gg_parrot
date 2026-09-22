import test from "node:test";
import assert from "node:assert/strict";
import {
  exitRules, fmtSignedMoney, fmtSignedPct, gaugeModel, runningFor, unrealizedMoney,
} from "../src/lib/positionExits.js";

test("청산 규칙 — A 는 익절·손절 양쪽, 값은 매크로에서", () => {
  const rules = exitRules({ rule_type: "A", params: { take_profit_pct: 5 }, risk: { stop_loss_pct: 3 } });
  assert.equal(rules.kind, "two");
  assert.deepEqual([rules.tp.label, rules.sl.label], ["익절 +5%", "손절 -3%"]);
  assert.equal(rules.summary, "익절 +5% · 손절 -3%");
});

test("청산 규칙 — 손절만(G) · 익절만(H, 손절 없음) · 아무것도 없음(J)", () => {
  const g = exitRules({ rule_type: "G", params: { period: 20 }, risk: { stop_loss_pct: 4 } });
  assert.equal(g.kind, "sl-only");
  assert.equal(g.summary, "손절 -4% · 익절은 신호");
  const h = exitRules({ rule_type: "H", params: { take_profit: 2.5 }, risk: {} });
  assert.equal(h.kind, "tp-only");
  assert.equal(h.summary, "익절 +2.5%");
  const f = exitRules({ rule_type: "F", params: { rsi_period: 14, take_profit: 4 }, risk: {} });
  assert.equal(f.kind, "tp-only");
  assert.equal(f.tp.label, "익절 +4%");
  const j = exitRules({ rule_type: "J", params: { fast_period: 5 }, risk: {} });
  assert.equal(j.kind, "none");
  assert.equal(j.summary, "전략 신호");
  assert.equal(exitRules(null).kind, "none");
});

test("청산 규칙 — E 트레일링과 K 숏은 각자의 값", () => {
  const e = exitRules({ rule_type: "E", params: { activation_profit: 5, trail_percent: 2 }, risk: { stop_loss_pct: 3 } });
  assert.equal(e.kind, "two");
  assert.equal(e.trailing.label, "발동 +5% · 고점 -2%");
  assert.equal(e.summary, "트레일링 발동 +5% · 고점 -2% · 손절 -3%");
  const k = exitRules({ rule_type: "K", position_side: "short", params: { short_take_profit_pct: 4, short_stop_loss_pct: 2, long_take_profit_pct: 9 }, risk: {} });
  assert.deepEqual([k.tp.pct, k.sl.pct], [4, 2]);
  const kl = exitRules({ rule_type: "K", position_side: "long", params: { long_take_profit_pct: 9 }, risk: { stop_loss_pct: 3 } });
  assert.deepEqual([kl.tp.pct, kl.sl.pct], [9, 3]);
});

test("게이지 — 손절에서 익절까지 위치, 한쪽만 있으면 그쪽 폭을 반대편에도", () => {
  const two = gaugeModel(3.42, exitRules({ rule_type: "A", params: { take_profit_pct: 5 }, risk: { stop_loss_pct: 3 } }));
  assert.equal(two.show, true);
  assert.equal(Math.round(two.position * 1000) / 1000, 0.842);
  assert.equal(two.oneSided, false);
  assert.deepEqual([two.left.tone, two.right.tone], ["is-down", "is-up"]);
  const loss = gaugeModel(-1.5, exitRules({ rule_type: "A", params: { take_profit_pct: 5 }, risk: { stop_loss_pct: 3 } }));
  assert.equal(loss.position, 0.25);
  assert.equal(gaugeModel(-9, exitRules({ rule_type: "A", params: { take_profit_pct: 5 }, risk: { stop_loss_pct: 3 } })).position, 0, "손절 아래는 왼쪽 끝에서 멈춘다");
  const slOnly = gaugeModel(2, exitRules({ rule_type: "G", params: {}, risk: { stop_loss_pct: 4 } }));
  assert.equal(slOnly.oneSided, true);
  assert.equal(slOnly.position, 0.75, "오른쪽도 손절 폭(4%)으로 잰다");
  assert.equal(slOnly.right.label, "익절 규칙 없음 · 신호 청산");
  assert.equal(slOnly.right.tone, "is-muted");
  assert.equal(gaugeModel(1, exitRules({ rule_type: "J", params: {}, risk: {} })).show, false);
});

test("게이지 — 트레일링은 발동 전엔 발동 값, 발동 뒤엔 추적 중", () => {
  const rules = exitRules({ rule_type: "E", params: { activation_profit: 5, trail_percent: 2 }, risk: { stop_loss_pct: 3 } });
  assert.equal(gaugeModel(1, rules).right.label, "발동 +5% · 고점 -2%");
  assert.equal(gaugeModel(6.2, rules).right.label, "고점 -2% 추적 중");
  assert.equal(gaugeModel(6.2, rules).position, 1);
});

test("평가손익 금액 · 부호 표기 · 실행 시간", () => {
  assert.equal(Math.round(unrealizedMoney({ entry_price: 74453.68, last_price: 76874.26, position_qty: 0.0129, position_side: "long" }) * 100) / 100, 31.23);
  assert.equal(Math.round(unrealizedMoney({ entry_price: 100, last_price: 90, position_qty: 2, position_side: "short" }) * 100) / 100, 20);
  assert.equal(unrealizedMoney({ entry_price: 0, last_price: 1, position_qty: 1 }), null);
  assert.equal(fmtSignedPct(3.416), "+3.42%");
  assert.equal(fmtSignedPct(-1.87), "-1.87%");
  assert.equal(fmtSignedPct(0), "0.00%");
  assert.equal(fmtSignedMoney(31.226, "BTCUSDT"), "+31.23 USDT");
  assert.equal(fmtSignedMoney(-17.96, "ETHUSDC"), "-17.96 USDC");
  const t0 = Date.parse("2026-09-15T10:00:00Z");
  assert.equal(runningFor("2026-09-15T07:46:00Z", t0), "2시간 14분");
  assert.equal(runningFor("2026-09-15T09:12:00Z", t0), "48분");
  assert.equal(runningFor("2026-09-12T08:00:00Z", t0), "3일 2시간");
  assert.equal(runningFor("2026-09-15T09:59:40Z", t0), "1분 미만");
  assert.equal(runningFor("", t0), "");
});

// 투입금 대비 총수익률 (2026-09-22): 구동 중 큰 숫자는 서버 return_pct, 진입가 대비 %는 보조로.
import { headlineReturn } from "../src/lib/positionExits.js";

test("headlineReturn prefers total return over invested capital and keeps entry-based pct as a note", () => {
  const live = headlineReturn({ in_position: true, unrealized_pct: 1.2, realized_pnl: 0.4, invested_usdt: 40, return_pct: 2.2 });
  assert.deepEqual(live, { pct: 2.2, label: "투입금 대비 총수익률", note: "진입가 대비 +1.20% · 투입 40 USDT" });
  const flat = headlineReturn({ in_position: false, unrealized_pct: 0, realized_pnl: 0.8, invested_usdt: 40, return_pct: 2.0 });
  assert.deepEqual(flat, { pct: 2.0, label: "투입금 대비 총수익률", note: "실현 +0.80 USDT · 투입 40 USDT" });
  const legacy = headlineReturn({ in_position: true, unrealized_pct: 1.2, realized_pnl: 0, invested_usdt: 0, return_pct: null });
  assert.deepEqual(legacy, { pct: 1.2, label: "평가손익", note: "" });
});
