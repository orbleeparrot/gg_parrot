import assert from "node:assert/strict";
import test from "node:test";

import { macroFileName } from "../src/lib/macroFileName.js";

const at = new Date("2026-10-13T06:00:00Z"); // KST 10/13 15:00

test("매크로 파일 이름은 영문 거래소_종목_전략_방향_날짜", () => {
  assert.equal(macroFileName({ exchange: "bithumb", symbol: "KRW-BTR", rule_type: "F", position_side: "long" }, at),
    "bithumb_KRW-BTR_rsi_long_20261013.ggm.json");
  assert.equal(macroFileName({ symbol: "BTCUSDT", rule_type: "A", position_side: "short" }, at),
    "binance_BTCUSDT_tp-sl-reentry_short_20261013.ggm.json");
  assert.equal(macroFileName({ exchange: "upbit", symbols: ["KRW-XRP", "KRW-ETH", "KRW-SOL"], rule_type: "D" }, at),
    "upbit_KRW-XRP-plus2_grid_long_20261013.ggm.json");
});

test("한글 · 특수문자 · 빈 값은 빼고 영문만 남긴다", () => {
  const name = macroFileName({ exchange: "모름", symbol: "비트/코인:*", rule_type: "Z", position_side: "long" }, at);
  assert.equal(name, "binance_macro_z_long_20261013.ggm.json");
  assert.match(name, /^[A-Za-z0-9._-]+$/);
});

test("KST 자정을 넘기면 다음 날짜", () => {
  assert.equal(macroFileName({ symbol: "BTCUSDT", rule_type: "C" }, new Date("2026-10-13T15:30:00Z")),
    "binance_BTCUSDT_dca_long_20261014.ggm.json");
});
