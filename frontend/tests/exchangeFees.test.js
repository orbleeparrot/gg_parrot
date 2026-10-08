// 거래소별 수수료 (2026-10-08) — 표가 파이썬과 자바스크립트 두 벌로 있다.
//
// 예전에는 양쪽 모두 거래소와 무관하게 0.1%(바이낸스 요율)를 썼고, 그래서 국내 백테스트가
// 실제보다 나쁘게 나왔다(업비트 원화마켓은 0.05%). 표를 둘로 두는 대신 **대조한다** —
// 한쪽만 고치면 같은 매크로가 서버에서 다르게 계산된다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { EXCHANGES, commissionForExchange } from "../src/lib/exchanges.js";
import { defaultForm, withExchangeDefaults } from "../src/lib/macro.js";

// backend/app/exchanges.py 의 _COMMISSION_PCT 를 읽는다.
function backendTable() {
  const source = readFileSync(new URL("../../backend/app/exchanges.py", import.meta.url), "utf8");
  const match = /_COMMISSION_PCT = \{([^}]+)\}/.exec(source);
  assert.ok(match, "backend 의 _COMMISSION_PCT 를 찾지 못했다 — 이름이 바뀌었으면 이 시험을 고쳐라");
  const table = {};
  for (const [, name, value] of match[1].matchAll(/"([a-z]+)":\s*([0-9.]+)/g)) {
    table[name] = Number(value);
  }
  return table;
}

test("두 언어의 수수료 표가 같다", () => {
  const backend = backendTable();
  assert.ok(Object.keys(backend).length >= 3, "표를 못 읽었다면 이 시험이 아무것도 보지 않는 것이다");
  for (const item of EXCHANGES) {
    assert.ok(item.value in backend, `backend 에 ${item.value} 가 없다`);
    assert.equal(item.commission, backend[item.value],
      `${item.value}: 화면 ${item.commission}% vs 서버 ${backend[item.value]}%`);
  }
  assert.equal(Object.keys(backend).length, EXCHANGES.length, "한쪽에만 있는 거래소가 있다");
});

test("국내가 바이낸스보다 싸고, 아무도 0 이 아니다", () => {
  const binance = commissionForExchange("binance");
  assert.ok(commissionForExchange("upbit") < binance);
  assert.ok(commissionForExchange("bithumb") < binance);
  // 0 은 "마찰이 없다" 로 읽혀 가장 위험한 쪽으로 틀린다(빗썸 API 무료는 이벤트다).
  for (const item of EXCHANGES) assert.ok(item.commission > 0, `${item.value} 가 0 이다`);
});

test("거래소를 바꾸면 조건 판의 수수료도 따라간다", () => {
  const binance = defaultForm();
  assert.equal(binance.commission_pct, commissionForExchange("binance"),
    "기본 폼의 수수료가 바이낸스 요율이어야 한다");
  const upbit = withExchangeDefaults(binance, "upbit", []);
  assert.equal(upbit.commission_pct, commissionForExchange("upbit"));
  const bithumb = withExchangeDefaults(upbit, "bithumb", []);
  assert.equal(bithumb.commission_pct, commissionForExchange("bithumb"));
  // 돌아올 때도 따라와야 한다 — 한쪽만 바뀌면 국내 요율이 바이낸스 백테스트에 남는다.
  assert.equal(withExchangeDefaults(bithumb, "binance", []).commission_pct,
    commissionForExchange("binance"));
});
