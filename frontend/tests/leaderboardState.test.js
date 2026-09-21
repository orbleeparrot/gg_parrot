import assert from "node:assert/strict";
import test from "node:test";
import { cooldownLabel, isLive, liveReturn, stateLine, symbolsOf } from "../src/lib/leaderboardState.js";

const holding = { state: "holding", return_pct: 1.5, equity: 1015, virtual_balance: 1000,
  legs: [{ symbol: "BTCUSDT", qty: 2, dir: 1, last_price: 100, in_position: true }] };

test("liveReturn moves with price for a long, inversely for a short, sums a portfolio, falls back without prices", () => {
  assert.equal(liveReturn(holding, { BTCUSDT: 101 }), 1.7);           // 1015 + 2*1 = 1017 → 1.7%
  const short = { ...holding, legs: [{ ...holding.legs[0], dir: -1 }] };
  assert.equal(liveReturn(short, { BTCUSDT: 101 }), 1.3);
  const port = { ...holding, legs: [holding.legs[0], { symbol: "ETHUSDT", qty: 10, dir: 1, last_price: 10, in_position: true }] };
  assert.equal(liveReturn(port, { BTCUSDT: 101, ETHUSDT: 10.5 }), 2.2); // +2 +5 → 1022
  assert.equal(liveReturn(port, { BTCUSDT: 101 }), 1.5);               // ETH 없음 → 서버값
  assert.equal(liveReturn({ ...holding, state: "waiting" }, { BTCUSDT: 101 }), 1.5);
  assert.equal(liveReturn({ ...holding, virtual_balance: null }, { BTCUSDT: 101 }), 1.5);
  assert.equal(liveReturn({ ...holding, equity: undefined }, { BTCUSDT: 101 }), 1.5);
  assert.equal(isLive(holding, { BTCUSDT: 101 }), true);
  assert.equal(isLive(holding, {}), false);
  assert.equal(isLive({ ...holding, equity: null }, { BTCUSDT: 101 }), false);
});

test("stateLine renders the five states", () => {
  const now = 1_700_000_000_000;
  assert.deepEqual(stateLine({ state: "waiting", symbol: "BTCUSDT", last_price: 96412.1, trade_count: 0 }, now), { text: "진입 대기 · BTC 96,412.1", tone: "muted" });
  assert.deepEqual(stateLine({ state: "waiting", symbol: "ONEUSDT", last_price: null, trade_count: 0 }, now), { text: "진입 대기", tone: "muted" });
  assert.deepEqual(stateLine({ ...holding, trade_count: 1, legs: [{ ...holding.legs[0], entry_price: 100 }] }, now), { text: "보유 중 · 진입가 100 · 거래 1회", tone: "live" });
  assert.deepEqual(stateLine({ state: "exited", last_fill_kst: "12:41", last_fill_kind: "tp", last_fill_return: 1.8, cooldown_until_ms: null, trade_count: 2 }, now), { text: "12:41 익절 +1.80% · 재진입 대기 · 거래 2회", tone: "good" });
  assert.deepEqual(stateLine({ state: "exited", last_fill_kst: "12:41", last_fill_kind: "sl", last_fill_return: -0.9, cooldown_until_ms: now + 14 * 60_000 + 1, trade_count: 2 }, now), { text: "12:41 손절 -0.90% · 쿨다운 15분 · 거래 2회", tone: "bad" });
  assert.deepEqual(stateLine({ state: "halted", trade_count: 3 }, now), { text: "일일 손실 한도 · 내일 재개 · 거래 3회", tone: "warn" });
  assert.deepEqual(stateLine({ state: "stopped", return_pct: 0.4, trade_count: 0 }, now), { text: "종료됨 · 최종 +0.40%", tone: "muted" });
  assert.equal(stateLine({ state: "none" }, now), null);
});

test("cooldownLabel and symbolsOf", () => {
  const now = 1_700_000_000_000;
  assert.equal(cooldownLabel(now + 61_000, now), "쿨다운 2분");
  assert.equal(cooldownLabel(now - 1, now), "재진입 대기");
  assert.equal(cooldownLabel(null, now), "재진입 대기");
  const items = [holding, { ...holding, legs: [{ symbol: "ETHUSDT", qty: 1, dir: 1, last_price: 1, in_position: true }, { symbol: "BTCUSDT", qty: 0, dir: 1, last_price: 1, in_position: false }] }, { state: "waiting", legs: [{ symbol: "SOLUSDT", in_position: false }] }];
  assert.deepEqual(symbolsOf(items), ["BTCUSDT", "ETHUSDT"]);
});
