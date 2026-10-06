import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

import { formatQuoteAmount, practiceModeLabel } from "../src/lib/exchanges.js";
import { fmtSignedMoney, headlineReturn } from "../src/lib/positionExits.js";
import { describeRunOutcome, formatSignedMoney } from "../src/features/agents/runOutcome.js";
import { environmentLabel } from "../src/features/agents/history.js";
import { runnerKeyGuide } from "../src/lib/runnerGuide.js";

// 국내 거래소를 켠 뒤 사용자가 보는 면: 통화 · 연습 환경 이름 · 설치 안내가 거래소에 맞는 말을 하는가.
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8").replace(/\r\n/g, "\n");

// --- 통화: 세션의 종목에서 뽑는다 -------------------------------------------------
test("금액은 종목의 통화로 쓰고 원화는 소수점을 쓰지 않는다", () => {
  assert.equal(formatQuoteAmount(100000, "KRW-BTC"), "100,000 KRW");
  assert.equal(formatQuoteAmount(123456.4, "KRW-BTC"), "123,456 KRW");
  assert.equal(formatQuoteAmount(0, "KRW-BTC", { fixed: true }), "0 KRW");
  assert.equal(formatQuoteAmount(100000, "BTCUSDT"), "100,000 USDT");
  assert.equal(formatQuoteAmount(12.5, "BTCUSDT", { fixed: true }), "12.50 USDT");
  assert.equal(formatQuoteAmount(12.5, "BTCUSDT"), "12.5 USDT");
});

test("부호 있는 손익 — 원화는 원 단위, USDT 는 그대로 두 자리", () => {
  assert.equal(fmtSignedMoney(31226.7, "KRW-ETH"), "+31,227 KRW");
  assert.equal(fmtSignedMoney(-17960, "KRW-ETH"), "-17,960 KRW");
  assert.equal(fmtSignedMoney(31.226, "BTCUSDT"), "+31.23 USDT");
  assert.equal(formatSignedMoney(-4200, "KRW-BTC"), "−4,200 KRW");
  assert.equal(formatSignedMoney(128.4, "ZECUSDT"), "+128.40 USDT");
});

const krw = {
  session_id: 7, symbol: "KRW-BTC", market: "spot", leverage: 1, position_side: "long", testnet: false,
  started_kst: "10/06 09:00:00", stopped_kst: "10/06 10:00:00", last_price: 95000000,
  realized_pnl: 4200, invested_usdt: 100000, return_pct: 4.2, unrealized_pct: 0, in_position: false,
  position_uncertain: false, note: "청산 완료 후 종료", stop_mode: "close_and_stop", status: "stopped",
};

test("종료 결과 — 원화 세션의 투입금·손익이 원화로 나온다", () => {
  const outcome = describeRunOutcome(krw);
  assert.equal(outcome.rows.find((row) => row.label === "투입금").value, "100,000 KRW");
  assert.equal(outcome.pnl.sub, "+4,200 KRW · 투입 100,000 KRW");
  const legacy = describeRunOutcome({ ...krw, invested_usdt: 0, return_pct: null });
  assert.equal(legacy.pnl.text, "+4,200 KRW");
  for (const text of [outcome.pnl.sub, legacy.pnl.text, ...outcome.rows.map((row) => row.value)]) assert.doesNotMatch(String(text), /USDT/);
});

test("종료 결과 — USDT 세션은 그대로 USDT 다", () => {
  const outcome = describeRunOutcome({ ...krw, symbol: "BTCUSDT", realized_pnl: 4.2, invested_usdt: 100 });
  assert.equal(outcome.rows.find((row) => row.label === "투입금").value, "100 USDT");
  assert.equal(outcome.pnl.sub, "+4.20 USDT · 투입 100 USDT");
});

test("포지션 스트립의 큰 숫자 보조 문구도 세션 통화를 따른다", () => {
  const flat = headlineReturn({ symbol: "KRW-BTC", in_position: false, realized_pnl: 4200, invested_usdt: 100000, return_pct: 4.2 });
  assert.equal(flat.note, "실현 +4,200 KRW · 투입 100,000 KRW");
  const usdt = headlineReturn({ symbol: "BTCUSDT", in_position: false, realized_pnl: 0.8, invested_usdt: 40, return_pct: 2 });
  assert.equal(usdt.note, "실현 +0.80 USDT · 투입 40 USDT");
});

test("통화를 글자로 박아 둔 화면이 남지 않았다", () => {
  for (const path of ["../src/components/PositionStrip.jsx", "../src/components/AgentHistory.jsx", "../src/components/RunnerSessions.jsx", "../src/components/RunResultScreen.jsx"]) {
    const source = read(path);
    assert.doesNotMatch(source.replace(/\/\/.*$/gm, ""), /USDT/, `${path} 에 USDT 글자가 박혀 있다`);
  }
});

// --- 연습 환경 이름: 국내에는 테스트넷이 없다 ---------------------------------------
test("국내 세션의 연습 표시는 모의, 바이낸스는 테스트넷", () => {
  assert.equal(practiceModeLabel("KRW-BTC"), "모의");
  assert.equal(practiceModeLabel("BTCUSDT"), "테스트넷");
  assert.equal(environmentLabel({ symbol: "KRW-BTC", testnet: true, market: "spot" }), "모의 · 현물");
  assert.equal(environmentLabel({ symbol: "BTCUSDT", testnet: true, market: "spot" }), "테스트넷 · 현물");
  assert.equal(environmentLabel({ symbol: "KRW-BTC", testnet: false, market: "spot" }), "실거래 · 현물");
  assert.match(describeRunOutcome({ ...krw, testnet: true }).rows.find((row) => row.label === "종목·환경").value, /모의$/);
});

// --- 설치·연결 안내 ----------------------------------------------------------------
const NO_TESTNET_REQUEST = /테스트넷(?!이 없)|testnet|demo/i;

for (const exchange of ["upbit", "bithumb"]) {
  test(`${exchange} 안내 — 테스트넷 키를 시키지 않고 모의 모드와 허용 IP 를 말한다`, () => {
    const guide = runnerKeyGuide({ exchange, symbol: "KRW-BTC" });
    assert.equal(guide.domestic, true);
    const text = JSON.stringify(guide, (_key, value) => (typeof value === "string" && /^https?:/.test(value) ? "" : value));
    assert.doesNotMatch(text, NO_TESTNET_REQUEST);
    assert.doesNotMatch(text, /바이낸스|Binance/);
    assert.match(text, new RegExp(guide.exchangeName));
    assert.match(guide.introTitle + guide.introBody + guide.keyDescription, /모의/);
    assert.match(guide.steps.map((step) => step.join(" ")).join(" "), /허용 IP/);
    assert.match(guide.launchKeyNote, /허용 IP/);
    assert.match(guide.modeShort, /모의/);
  });
}

test("업비트 안내만 허용 IP 10개 한도를 말한다", () => {
  const steps = (exchange) => runnerKeyGuide({ exchange }).steps.map((step) => step.join(" ")).join(" ");
  assert.match(steps("upbit"), /10개/);
  assert.doesNotMatch(steps("bithumb"), /10개/);
});

test("바이낸스 안내는 그대로 테스트넷이다", () => {
  const spot = runnerKeyGuide({ exchange: "binance", symbol: "BTCUSDT" });
  assert.equal(spot.domestic, false);
  assert.equal(spot.url, "https://testnet.binance.vision/");
  assert.equal(spot.introTitle, "현물 테스트넷 키가 필요해요.");
  assert.equal(spot.modeShort, "테스트넷 기본");
  assert.equal(spot.storageKey, "ggparrot:binance-testnet-key-ready:v1:spot");
  assert.equal(runnerKeyGuide({ symbol: "BTCUSDT", position_side: "short" }).storageKey, "ggparrot:binance-testnet-key-ready:v1:futures");
  assert.equal(runnerKeyGuide(null).exchangeName, "바이낸스");
});

test("거래소마다 '키 준비했어요' 확인이 따로 저장된다", () => {
  const keys = ["upbit", "bithumb", "binance"].map((exchange) => runnerKeyGuide({ exchange }).storageKey);
  assert.equal(new Set(keys).size, 3);
});

test("마법사는 안내 모듈의 글만 쓰고 바이낸스 문구를 박아 두지 않는다", () => {
  const wizard = read("../src/pages/RunnerDownload.jsx");
  assert.match(wizard, /runnerKeyGuide\(selected\?\.macro\)/);
  assert.doesNotMatch(wizard, /BINANCE_TESTNET_GUIDES|바이낸스 키를|테스트넷 기본|테스트넷 · 가짜 자금|테스트넷 키/);
  const install = read("../src/pages/RunnerInstall.jsx");
  assert.match(install, /업비트·빗썸[\s\S]{0,60}모의 모드/);
  assert.match(install, /허용 IP/);
});

test("PaperPanel 의 사용법 링크는 국내에서도 열려 있다", () => {
  const paper = read("../src/components/PaperPanel.jsx");
  const link = paper.indexOf('to="/?run=1&step=1"');
  assert.ok(link > 0);
  assert.doesNotMatch(paper.slice(Math.max(0, link - 60), link), /!domestic/);
});
