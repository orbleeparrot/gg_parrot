// 묶음(비중 지정)을 화면이 제대로 읽는지 — 종목 목록은 공용 macroSymbols 로, '균등이냐 비중이냐' 는
// 공용 isEvenWeights 로 읽어야 한다. 소비처가 `macro.symbols` 를 직접 보면 비중 묶음이 단일 종목으로
// 그려지고, 같은 카드 안의 '종목 비중' 행과 모순된다(실측 버그).
//
// 화면 파일(페이지 · 큰 패널)은 라우터 · 인증 · api 를 끌고 와서 통째로 그릴 수 없다 — 이 저장소의
// 다른 시험(studioProRoute · domesticRunner)처럼 소스를 읽어 배선을 못 박는다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";
import { macroSymbols } from "../src/lib/portfolio.js";
import { GLOSSARY } from "../src/lib/glossary.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

// legs(비중 묶음)를 모르면 단일 종목으로 그려지던 자리들.
const CONSUMERS = [
  ["src/pages/Studio.jsx", "../src/pages/Studio.jsx"],
  ["src/components/ResultView.jsx", "../src/components/ResultView.jsx"],
  ["src/components/StudioDock.jsx", "../src/components/StudioDock.jsx"],
  ["src/components/RegisterMacroModal.jsx", "../src/components/RegisterMacroModal.jsx"],
  ["src/components/PaperPanel.jsx", "../src/components/PaperPanel.jsx"],
  ["src/components/AskParrotDialog.jsx", "../src/components/AskParrotDialog.jsx"],
];

test("종목 목록을 꺼내는 소비처는 공용 함수를 쓴다", () => {
  for (const [label, path] of CONSUMERS) {
    const src = read(path);
    if (label === "src/components/ResultView.jsx") {
      // 종목별 성과 표는 종목 목록이 아니라 '균등이냐' 만 본다.
      assert.match(src, /isEvenWeights/, label);
      continue;
    }
    assert.match(src, /macroSymbols/, `${label} 가 macroSymbols 를 쓰지 않는다`);
  }
});

test("소비처가 macro.symbols 를 직접 보고 묶음을 가리지 않는다", () => {
  // `macro.symbols.length > 1` 로 묶음을 가리면 legs 형태(비중 지정)가 단일 종목으로 떨어진다.
  for (const [label, path] of CONSUMERS) {
    const src = read(path);
    assert.doesNotMatch(src, /macro\.symbols\s*&&\s*macro\.symbols\.length\s*>\s*1/, label);
    assert.doesNotMatch(src, /Array\.isArray\(macro\.symbols\)\s*&&\s*macro\.symbols\.length\s*>\s*1/, label);
    assert.doesNotMatch(src, /macro\.symbols\?\.length\s*\?/, label);
  }
});

test("Studio 의 매크로 카드 종목이 macroSymbols 에서 나온다 (비중 묶음이 단일 종목으로 그려지던 자리)", () => {
  const src = read("../src/pages/Studio.jsx");
  assert.match(src, /const cardSymbols = macroSymbols\(cardMacro\)/);
});

test("모의 세션 설명은 비중을 정한 묶음에 '균등' 이라고 말하지 않는다", () => {
  // 서버 세션은 이미 비중대로 돈다 — 화면만 균등이라고 말하면 거짓이다.
  for (const path of ["../src/components/PaperPanel.jsx", "../src/components/StudioDock.jsx"]) {
    const src = read(path);
    assert.match(src, /isEvenWeights\(macro\.legs\) \? "수로 똑같이" : "비중대로"/, path);
    assert.doesNotMatch(src, /자본을 종목 수로 나눠/, path);
  }
});

test("비중 묶음 카드는 종목을 모두 보여 주고 '비중 지정' 이라 적는다", async () => {
  // Studio 가 넘기는 종목 목록(macroSymbols)과 카드 안의 비중 행이 같은 매크로를 가리키는지.
  const macro = {
    exchange: "binance", symbol: "BTCUSDT", rule_type: "I", candle_interval: "1h",
    params: { k: 0.5, initial_capital: 1000 }, risk: { invest_ratio: 1 }, period: { preset: "3m" },
    legs: [{ symbol: "BTCUSDT", weight: 70 }, { symbol: "ETHUSDT", weight: 30 }],
  };
  const text = textOf(await renderComponent("src/components/MacroCard.jsx",
    { macro, symbols: macroSymbols(macro) }));
  assert.match(text, /BTC · ETH/, "제목이 두 종목을 보여 준다");
  assert.match(text, /2종목 비중 지정/);
  assert.match(text, /BTC 70% · ETH 30%/);
  assert.doesNotMatch(text, /자금 균등/);
});

test("균등 비중 legs 묶음은 카드에서도 '자금 균등' 이다 — 서버 요약과 같은 기준", async () => {
  // 옛 코드는 legs 가 있으면 무조건 '비중 지정' 이라 적어, 서버 요약('자금 균등')과 갈렸다.
  const macro = {
    exchange: "binance", symbol: "BTCUSDT", rule_type: "I", candle_interval: "1h",
    params: { k: 0.5, initial_capital: 1000 }, risk: { invest_ratio: 1 }, period: { preset: "3m" },
    legs: [{ symbol: "BTCUSDT", weight: 50 }, { symbol: "ETHUSDT", weight: 50 }],
  };
  const text = textOf(await renderComponent("src/components/MacroCard.jsx",
    { macro, symbols: macroSymbols(macro) }));
  assert.match(text, /2종목 자금 균등/);
  assert.doesNotMatch(text, /비중 지정/);
});

test("종목별 성과 머리말은 비중을 정한 묶음에 '자금 균등 분할' 이라 쓰지 않는다", async () => {
  const rows = [
    { symbol: "BTCUSDT", final_return_pct: 5, mdd_pct: 3, win_rate: 50, total_trades: 4 },
    { symbol: "ETHUSDT", final_return_pct: -2, mdd_pct: 6, win_rate: 40, total_trades: 3 },
  ];
  const result = {
    initial_capital: 1000, final_equity: 1050, final_return_pct: 5, buy_hold_return_pct: 2,
    mdd_pct: 3, win_rate_pct: 50, total_trades: 7, sharpe: 1.2, profit_factor: 1.5,
    max_consecutive_losses: 2, equity_curve: [], trades: [],
  };
  const even = textOf(await renderComponent("src/components/ResultView.jsx",
    { result, perSymbol: rows, symbol: "BTCUSDT", summary: "", periodLabel: "", customizable: false }));
  assert.match(even, /자금 균등 분할/);
  const weighted = textOf(await renderComponent("src/components/ResultView.jsx",
    { result, perSymbol: rows, symbol: "BTCUSDT", summary: "", periodLabel: "", customizable: false,
      legs: [{ symbol: "BTCUSDT", weight: 70 }, { symbol: "ETHUSDT", weight: 30 }] }));
  assert.match(weighted, /종목마다 비중 지정/);
  assert.doesNotMatch(weighted, /자금 균등 분할/);
  // 안내 흐름이 레그를 실제로 넘긴다 — 안 넘기면 이 화면은 늘 '균등' 이라고 말한다.
  assert.match(read("../src/components/HeroGuideScreens.jsx"), /legs=\{backtest\.testedMacro\?\.legs \|\| null\}/);
});

// ── W3. 비중 · 묶음 한도 오류가 화면에 뜬다 ──
test("칸 없는 오류는 바닥 경고로 올라간다 (Studio)", () => {
  const src = read("../src/pages/Studio.jsx");
  // fieldErrorKey 가 '칸이 있는 오류' 만 담으므로, 바닥 경고의 `!fieldErrorKey` 조건이 칸 없는 오류를 받는다.
  assert.match(src, /FIELDLESS_ERROR_FIELDS/);
  assert.match(src, /const fieldErrorKey = fieldError && !FIELDLESS_ERROR_FIELDS\.includes\(fieldError\.field\)/);
  assert.match(src, /if \(valErr && !fieldErrorKey\) return \{ tone: "warn", text: valErr/);
});

test("프로 빌더도 칸 없는 오류를 검증 전에 보여 준다 (StudioPro)", () => {
  const src = read("../src/pages/StudioPro.jsx");
  assert.match(src, /FIELDLESS_ERROR_FIELDS\.includes\(problem\.field\)/);
  assert.match(src, /formProblem \? <p className="pro-error" role="alert">\{formProblem\.message\}<\/p> : null/);
});

// ── W9. 용어 설명이 비중 입력 옆에서 거짓이 되지 않는다 ──
test("용어 'symbols' 는 '균등하게 나눠, 같은 규칙' 이라고 단정하지 않는다", () => {
  // 비중 입력 · 레그 규칙 바로 옆에 뜨는 설명이다 — 단정하면 화면과 어긋난다.
  assert.doesNotMatch(GLOSSARY.symbols, /균등하게 나눠, 같은 규칙/);
  assert.match(GLOSSARY.symbols, /비중/, "비중을 정할 수 있다는 것을 말한다");
});

test("용어 'bundle_risk' 가 있고 묶음 한도 체크가 그것을 쓴다", () => {
  assert.ok(GLOSSARY.bundle_risk, "묶음 한도에 붙일 설명이 있어야 한다");
  assert.match(GLOSSARY.bundle_risk, /동시 보유|총 노출/);
  // chk 는 hint 를 받지 않는다 — 설명은 term(ⓘ)으로만 붙는다.
  assert.match(read("../src/components/Builder.jsx"), /chk\("use_bundle_risk", "묶음 한도 쓰기", \{ term: "bundle_risk" \}\)/);
});
