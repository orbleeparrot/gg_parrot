import assert from "node:assert/strict";
import test from "node:test";
import { coinTicker, futuresNote, sessionExchange, sessionStatus, strategyLine } from "../src/features/agents/sessionCoin.js";

test("큰 글씨 티커는 마켓 이름이 아니라 코인 — KRW-B3 → B3, BTCUSDT → BTC", () => {
  assert.equal(coinTicker("KRW-B3"), "B3");
  assert.equal(coinTicker("BTCUSDT"), "BTC");
  assert.equal(coinTicker("1000SHIBUSDT"), "1000SHIB");
  assert.equal(coinTicker(""), "");
});

test("거래소는 매크로에서 — 모르는 원화 종목은 업비트 · 빗썸 중 어디인지 모르니 작은 로고를 빼고, USDT 종목은 바이낸스", () => {
  assert.equal(sessionExchange({ symbol: "KRW-B3", macro: { exchange: "upbit" } }), "upbit");
  assert.equal(sessionExchange({ symbol: "KRW-FORT", macro: { exchange: "Bithumb" } }), "bithumb");
  assert.equal(sessionExchange({ symbol: "KRW-B3", macro: {} }), null);
  assert.equal(sessionExchange({ symbol: "BTCUSDT", macro: {} }), "binance");
});

test("마켓 이름 대신 남기는 건 바이낸스 선물의 '선물 N배'뿐", () => {
  assert.equal(futuresNote("futures", 3), "선물 3배");
  assert.equal(futuresNote("futures", 1), "선물");
  assert.equal(futuresNote("futures", undefined), "선물");
  assert.equal(futuresNote("spot", 3), "");
});

test("둘째 줄은 전략 이름(앞 글자표 없이) · 실전/모의/테스트넷", () => {
  assert.equal(strategyLine({ symbol: "KRW-B3", testnet: false, macro: { rule_type: "J" } }), "이동평균 크로스 · 실전");
  assert.equal(strategyLine({ symbol: "KRW-B3", testnet: true, mode: "mock", macro: { rule_type: "F" } }), "RSI 조건 매매 · 모의");
  assert.equal(strategyLine({ symbol: "BTCUSDT", testnet: true, macro: { rule_type: "E" } }), "트레일링 스탑 · 테스트넷");
});

test("목록 오른쪽 상태 — 종료 · 오류 · 응답대기가 먼저, 보유 중이면 손익, 비었으면 진입 대기", () => {
  assert.deepEqual(sessionStatus({ status: "stopped" }), { text: "종료", tone: "muted" });
  assert.deepEqual(sessionStatus({ status: "error", connected: true }), { text: "오류", tone: "down" });
  assert.deepEqual(sessionStatus({ status: "running", connected: false }), { text: "응답대기", tone: "warn" });
  assert.deepEqual(sessionStatus({ status: "running", connected: true, in_position: true, unrealized_pct: 1 }), { text: "+1.00%", tone: "up" });
  assert.deepEqual(sessionStatus({ status: "running", connected: true, in_position: true, unrealized_pct: -0.134 }), { text: "−0.13%", tone: "down" });
  assert.deepEqual(sessionStatus({ status: "running", connected: true, in_position: false }, { waiting: true }), { text: "진입 대기", tone: "warn" });
  assert.deepEqual(sessionStatus({ status: "running", connected: true, in_position: false }), { text: "실행 중", tone: "muted" });
});
