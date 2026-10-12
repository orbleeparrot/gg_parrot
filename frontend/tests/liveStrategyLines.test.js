// 실행 화면의 가격선은 서버가 준 값만 그린다 (2026-10-12).
//
// 왜: 업비트 KRW-CKB · E · 1시간봉 실거래에서 '진입 목표 -3%' 선이 가격을 따라 계속
// 내려왔다. 화면이 `마지막 종가 × 0.97` 로 그렸기 때문이다. 엔진은 평평해진 순간 잡힌
// **고정 기준가**를 보므로 그 선은 실제 진입 목표가 아니었다 — 떨어지는 장에서 영원히
// 안 닿는 선이 "곧 살 것처럼" 보였다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { computeSessionOverlay, computeStrategyOverlay, liveLevelsOverlay } from "../src/lib/indicators.js";

const candles = [2.9, 2.85, 2.8, 2.75, 2.73].map((c, i) => ({
  t: i * 3_600_000, o: c, h: c + 0.02, l: c - 0.02, c,
}));

const macro = {
  symbol: "KRW-CKB", exchange: "upbit", rule_type: "E", position_side: "long",
  market: "spot", leverage: 1, candle_interval: "1h",
  params: { entry_mode: "dip", entry_dip: 3, activation_profit: 4, trail_percent: 2,
            initial_capital: 10000 },
  risk: {}, fees: { commission_pct: 0.05, slippage_pct: 0.05 },
};

const labels = (overlay) => (overlay?.priceLines || []).map((line) => line.label);
const priceOf = (overlay, needle) =>
  (overlay?.priceLines || []).find((line) => line.label.includes(needle))?.price;

test("서버가 준 진입 목표를 그대로 그린다 — 현재가와 무관하다", () => {
  const overlay = liveLevelsOverlay({ entry_target: 2.7257, trail_stop: null,
                                      trail_activation: null, armed: false });
  assert.equal(priceOf(overlay, "진입 목표"), 2.7257);
  assert.match(overlay.note, /기준가에 묶여|따라 내려오지 않/, "왜 안 내려오는지 말해야 한다");
});

test("보유 중에는 발동 전·후를 다르게 그린다", () => {
  const before = liveLevelsOverlay({ entry_target: null, trail_stop: null,
                                     trail_activation: 100.88, armed: false });
  assert.deepEqual(labels(before).map((l) => l.split(" ")[0]), ["트레일"], "발동선만");
  assert.match(before.note, /그 전에는 켜지지 않/, "아직 안 켜졌다고 말해야 한다");

  const after = liveLevelsOverlay({ entry_target: null, trail_stop: 103.88,
                                    trail_activation: null, armed: true });
  assert.equal(priceOf(after, "트레일링 스탑"), 103.88);
});

test("선이 하나도 없고 서버가 맡은 것도 없으면 아무것도 그리지 않는다", () => {
  // E 가 아닌 규칙 — 서버는 이 선을 맡지 않는다. 미리보기가 그려도 된다.
  assert.equal(liveLevelsOverlay(null), null);
  assert.equal(liveLevelsOverlay({}), null);
  assert.equal(liveLevelsOverlay({ entry_target: 0, trail_stop: null, trail_activation: null }), null);
});

test("서버가 '아직 값이 없다' 고 하면 미리보기로 메우지 않는다", () => {
  // 바로 이 구멍이 버그였다: 서버는 entry_target: null 로 정직했는데 화면이 null 을
  // "관심 없음" 으로 읽고 '현재가 -3%' 미리보기를 그렸다. 가격이 내려가면 선도 내려왔다.
  const pending = { entry_target: null, trail_stop: null, trail_activation: null,
                    armed: false, pending: true };
  const live = liveLevelsOverlay(pending);
  assert.equal(live.priceLines.length, 0, "값이 없으면 선을 하나도 그리지 않는다");
  assert.equal(live.pending, true);
  assert.ok(live.legend.length > 0, "실행 화면은 minimal 이라 note 를 안 그린다 — 범례로 말해야 한다");
  assert.match(live.legend[0].label, /준비 중/);
  assert.match(live.note, /첫 봉/, "언제 나오는지 말해야 기다릴 수 있다");

  const overlay = computeSessionOverlay(macro, null, "long", candles, pending);
  assert.equal(labels(overlay).filter((l) => l.includes("진입 목표")).length, 0,
    "미리보기 진입 목표선이 되살아나면 안 된다 — 그게 가격을 따라 내려오던 그 선이다");
  assert.ok(!(overlay.series || []).some((item) => item.id === "trail"),
    "가정으로 만든 트레일링 스탑선도 같이 빠진다");
  assert.ok(overlay.legend.some((item) => /준비 중/.test(item.label)));
});

test("기다리는 중에도 내 평단은 그린다", () => {
  // 보유 중인데 고점을 잃은 옛 체크포인트 — 스탑선은 못 그려도 평단은 사실이다.
  const overlay = computeSessionOverlay(macro, 97, "long", candles,
    { entry_target: null, trail_stop: null, trail_activation: null, armed: true, pending: true });
  assert.ok(labels(overlay).some((l) => l.includes("내 평단")));
  assert.equal(labels(overlay).filter((l) => l.includes("진입 목표")).length, 0);
});

test("서버 선이 있으면 미리보기 선을 함께 그리지 않는다", () => {
  // 둘을 같이 그리면 '진입 목표' 가 두 개가 되고 그중 하나는 거짓이다.
  const preview = computeSessionOverlay(macro, null, "long", candles);
  assert.ok(labels(preview).some((l) => l.includes("진입 목표")), "서버 값이 없으면 미리보기를 그린다");
  const last = candles[candles.length - 1].c;
  assert.ok(Math.abs(priceOf(preview, "진입 목표") - last * 0.97) < 1e-9,
    "미리보기는 현재가 기준이다(빌더에서는 그게 맞다)");

  const live = computeSessionOverlay(macro, null, "long", candles,
    { entry_target: 2.7257, trail_stop: null, trail_activation: null, armed: false });
  const found = labels(live).filter((l) => l.includes("진입 목표"));
  assert.equal(found.length, 1, "진입 목표 선은 하나여야 한다");
  assert.equal(priceOf(live, "진입 목표"), 2.7257, "서버 값이 이겨야 한다");
  assert.ok(Math.abs(priceOf(live, "진입 목표") - last * 0.97) > 1e-6, "현재가 기준이면 안 된다");
});

test("가정으로 만든 트레일링 스탑선은 실행 화면에서 빠진다", () => {
  // 미리보기의 'trail' 선은 봉 종가의 누적 최고에서 되짚어 만든 가짜다 — 실제 고점이 아니다.
  const preview = computeSessionOverlay(macro, 97, "long", candles);
  assert.ok((preview.series || []).some((item) => item.id === "trail"), "미리보기에는 있다");
  const live = computeSessionOverlay(macro, 97, "long", candles,
    { entry_target: null, trail_stop: 103.88, trail_activation: null, armed: true });
  assert.ok(!(live.series || []).some((item) => item.id === "trail"),
    "서버가 실제 스탑선을 줬으면 가짜 선을 같이 그리면 안 된다");
  assert.equal(priceOf(live, "트레일링 스탑"), 103.88);
});

test("내 평단은 두 경우 모두 남는다", () => {
  const live = computeSessionOverlay(macro, 97, "long", candles,
    { entry_target: null, trail_stop: 103.88, trail_activation: null, armed: true });
  assert.ok(labels(live).some((l) => l.includes("내 평단")), "평단선은 서버 선과 무관하다");
});

test("빌더 미리보기는 그대로다", () => {
  // computeStrategyOverlay 는 '지금 가격에 진입한다고 가정' 하는 빌더용이다. 거기서는 맞다.
  const overlay = computeStrategyOverlay({ ...macro.params, rule_type: "E", position_side: "long" }, candles);
  assert.ok(labels(overlay).some((l) => l.includes("진입 목표")));
});

test("실행 화면이 서버 값을 실제로 넘긴다", () => {
  // 안 넘기면 화면은 조용히 미리보기 선으로 되돌아간다 — 바로 그 버그였다.
  const page = readFileSync(new URL("../src/pages/Agents.jsx", import.meta.url), "utf8");
  assert.match(page, /selected\?\.strategy_levels \|\| null/, "오버레이에 서버 값을 넘겨야 한다");
  assert.match(page, /selected\?\.strategy_levels\]/, "값이 바뀌면 다시 그려야 한다(의존성)");
});
