import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

import { formatQuoteAmount, practiceModeLabel } from "../src/lib/exchanges.js";
import { fmtSignedMoney, headlineReturn } from "../src/lib/positionExits.js";
import { describeRunOutcome, formatSignedMoney } from "../src/features/agents/runOutcome.js";
import { environmentLabel } from "../src/features/agents/history.js";
import { launchMinVersionFor, runnerKeyGuide } from "../src/lib/runnerGuide.js";
import { resolveRunnerDownload } from "../src/lib/runnerDownload.js";
import { api } from "../src/api.js";
import { renderComponent, textOf } from "./renderHelper.js";

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

// 소스에 글자가 있느냐가 아니라 화면에 나오는 글을 본다 — 컴포넌트를 실제로 그려서 확인한다.
const live = {
  session_id: 7, symbol: "KRW-BTC", market: "spot", leverage: 1, position_side: "long", testnet: true,
  status: "running", connected: true, realized_pnl: 4200, invested_usdt: 100000, return_pct: 4.2,
  unrealized_pct: 0, in_position: false, started_at: new Date().toISOString(),
};
const done = { ...live, status: "stopped", stop_mode: "close_and_stop", started_kst: "10/06 09:00:00", stopped_kst: "10/06 10:00:00",
  started_at: "2026-10-06T00:00:00Z", stopped_at: "2026-10-06T01:00:00Z", macro: null };

test("포지션 스트립이 그린 글 — 원화 세션은 KRW, USDT 세션은 USDT", async () => {
  const krwText = textOf(await renderComponent("src/components/PositionStrip.jsx", { session: live, macro: null }));
  assert.match(krwText, /투입금 100,000 KRW/);
  assert.match(krwText, /실현손익 · 누적 \+4,200 KRW/);
  assert.doesNotMatch(krwText, /USDT/);
  const usdt = { ...live, symbol: "BTCUSDT", realized_pnl: 4.2, invested_usdt: 100 };
  const usdtText = textOf(await renderComponent("src/components/PositionStrip.jsx", { session: usdt, macro: null }));
  assert.match(usdtText, /투입금 100 USDT/);
  assert.match(usdtText, /실현손익 · 누적 \+4.20 USDT/);
  assert.doesNotMatch(usdtText, /KRW/);
});

test("종료 결과 화면이 그린 글 — 마지막 안내의 단위가 실제 통화로 나오고 코드가 새지 않는다", async () => {
  const krwText = textOf(await renderComponent("src/components/RunResultScreen.jsx", { session: done }));
  assert.match(krwText, /누적값\(KRW\)이에요\. 거래소 체결 내역과 대조해 확인하세요\./);
  assert.match(krwText, /투입금 100,000 KRW/);
  const usdtText = textOf(await renderComponent("src/components/RunResultScreen.jsx", { session: { ...done, symbol: "BTCUSDT", realized_pnl: 4.2, invested_usdt: 100 } }));
  assert.match(usdtText, /누적값\(USDT\)이에요\./);
  for (const text of [krwText, usdtText]) assert.doesNotMatch(text, /[{}]|quoteOf|undefined/);
  const pending = textOf(await renderComponent("src/components/RunResultScreen.jsx", { session: { ...done, status: "running", stopping: true } }));
  assert.match(pending, /확정 보고를 보내면/);
});

test("결과 안내문은 종료 처리 중과 끝난 뒤가 다르고 단위가 통화를 따른다", () => {
  assert.equal(describeRunOutcome(krw).note, "실현손익은 실행기가 보고한 누적값(KRW)이에요. 거래소 체결 내역과 대조해 확인하세요.");
  assert.match(describeRunOutcome({ ...krw, symbol: "BTCUSDT" }).note, /누적값\(USDT\)/);
  assert.match(describeRunOutcome({ ...krw, status: "running", stopping: true }).note, /확정 보고/);
});

test("에이전트 기록 목록이 그린 글 — 코인 옆 단위와 손익이 원화다", async () => {
  const html = await renderComponent("src/components/AgentHistory.jsx", { sessions: [done], policy: {}, onOpen() {}, onTogglePin() {} }, { router: true, exportName: "AgentHistoryList" });
  const text = textOf(html);
  assert.match(text, /BTC KRW/);
  assert.match(text, /\+4,200 KRW/);
  assert.match(text, /모의 · 현물/);
  assert.doesNotMatch(text, /USDT|KRW-BTC/);
});

// 그릴 수 없는 파일(RunnerSessions 는 훅·네트워크가 얽혀 있다)에는 안전망으로 헬퍼를 쓰는지만 본다.
test("RunnerSessions 는 통화 헬퍼를 쓴다", () => {
  const source = read("../src/components/RunnerSessions.jsx");
  assert.match(source, /formatQuoteAmount\(s\.realized_pnl \?\? 0, s\.symbol/);
  assert.match(source, /practiceModeLabel\(s\.symbol, s\.mode\)/);
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

// 바이낸스 모의는 테스트넷이 아니다 — 주문이 어느 거래소에도 닿지 않는다. testnet 플래그 하나로는
// 그 셋째 모드를 말할 수 없어서 실행기가 세션에 mode 를 남긴다. 그 값이 있으면 그 말을 쓴다.
test("실행 모드를 보낸 세션은 모드가 연습 이름을 정한다", () => {
  assert.equal(practiceModeLabel("BTCUSDT", "mock"), "모의");
  assert.equal(practiceModeLabel("BTCUSDT", "testnet"), "테스트넷");
  assert.equal(practiceModeLabel("KRW-BTC", "mock"), "모의");
  assert.equal(environmentLabel({ symbol: "BTCUSDT", testnet: true, mode: "mock", market: "futures", leverage: 5 }),
    "모의 · 선물 5배");
  assert.match(describeRunOutcome({ ...krw, symbol: "BTCUSDT", testnet: true, mode: "mock" })
    .rows.find((row) => row.label === "종목·환경").value, /모의$/);
});

test("모드를 보내지 않은 옛 세션은 종목으로 가른다", () => {
  for (const mode of [undefined, "", null, "nonsense"]) {
    assert.equal(practiceModeLabel("BTCUSDT", mode), "테스트넷");
    assert.equal(practiceModeLabel("KRW-BTC", mode), "모의");
  }
  assert.equal(environmentLabel({ symbol: "BTCUSDT", testnet: true, market: "spot" }), "테스트넷 · 현물");
});

test("실거래 세션은 모드가 무엇이라 적혀 있어도 실거래다", () => {
  assert.equal(environmentLabel({ symbol: "BTCUSDT", testnet: false, mode: "mock", market: "spot" }), "실거래 · 현물");
  assert.match(describeRunOutcome({ ...krw, testnet: false, mode: "mock" })
    .rows.find((row) => row.label === "종목·환경").value, /메인넷\(실거래\)$/);
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

test("마법사가 읽는 안내 칸은 모든 거래소 안내에 실제 글로 채워져 있다", () => {
  const wizard = read("../src/pages/RunnerDownload.jsx");
  assert.match(wizard, /runnerKeyGuide\(selected\?\.macro\)/);
  const fields = [...new Set([...wizard.matchAll(/keyGuide\.(\w+)/g)].map((m) => m[1]))];
  assert.ok(fields.length >= 15, "마법사가 안내 객체를 거의 읽지 않는다");
  for (const exchange of ["binance", "upbit", "bithumb"]) {
    const guide = runnerKeyGuide({ exchange });
    for (const field of fields) {
      const value = guide[field];
      assert.notEqual(value, undefined, `${exchange} 안내에 ${field} 칸이 없다 — 화면에 undefined 가 나온다`);
      if (typeof value === "string") assert.ok(value.length > 0 && !/[{}]|undefined/.test(value), `${exchange}.${field}: ${value}`);
    }
  }
  // 바이낸스 문구를 화면에 다시 박아 두지 않았는지(위의 거래소별 안내가 유일한 출처).
  assert.doesNotMatch(wizard, /BINANCE_TESTNET_GUIDES|바이낸스 키를|테스트넷 기본|테스트넷 · 가짜 자금|테스트넷 키/);
  const install = read("../src/pages/RunnerInstall.jsx");
  assert.match(install, /업비트·빗썸[\s\S]{0,60}모의 모드/);
  assert.match(install, /허용 IP/);
});

test("'자동 연결 최소 버전' 칸 — 국내 매크로는 국내 요구 버전, 모르면 비운다", () => {
  const binance = runnerKeyGuide({ exchange: "binance" });
  const upbit = runnerKeyGuide({ exchange: "upbit" });
  const versions = { general: "6", domestic: "10" };
  assert.equal(launchMinVersionFor(binance, versions), "6");
  assert.equal(launchMinVersionFor(upbit, versions), "10");
  assert.equal(launchMinVersionFor(upbit, { general: "6" }), "", "국내인데 바이낸스 숫자를 보이면 안 된다");
  assert.equal(resolveRunnerDownload({ available: true, domestic_min_runner_version: "10", min_runner_version: "6" }, null).domesticMinVersion, "10");
  assert.equal(resolveRunnerDownload({ available: true, min_runner_version: "6" }, null).domesticMinVersion, "");
  assert.match(read("../src/pages/RunnerDownload.jsx"), /launchMinVersionFor\(keyGuide/);
});

test("매크로 파일 내려받기 실패는 서버가 말한 이유를 그대로 던진다", async (t) => {
  const reason = "매크로 파일은 바이낸스 전용입니다. 업비트·빗썸 매크로는 빠른 실행으로 실행기에 직접 연결해 주세요.";
  t.mock.method(globalThis, "fetch", async () => Response.json({ detail: reason }, { status: 422 }));
  await assert.rejects(api.downloadMacroFile({ exchange: "upbit", rule_type: "A", position_side: "long" }), { message: reason });
  t.mock.method(globalThis, "fetch", async () => new Response("<html>oops</html>", { status: 500 }));
  await assert.rejects(api.downloadMacroFile({ rule_type: "A", position_side: "long" }), { message: "매크로 파일 생성 실패" });
});

test("PaperPanel 의 사용법 링크는 국내에서도 열려 있다", () => {
  const paper = read("../src/components/PaperPanel.jsx");
  const link = paper.indexOf('to="/?run=1&step=1"');
  assert.ok(link > 0);
  assert.doesNotMatch(paper.slice(Math.max(0, link - 60), link), /!domestic/);
});
