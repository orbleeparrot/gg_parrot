// 코치 질문 그래프 전수 경로 — 스펙 §6 의 보증을 만드는 시험.
//
// 코치가 낼 수 있는 폼은 그래프의 경로 수만큼이고 그 수는 유한하다. 그래서 요청마다 서버가 폼을
// 검사하는 대신 **빌드 때 전수로** 증명한다 — 파이썬에 폼 빌더(buildMacro · defaultForm)를 한 벌 더
// 베끼지 않기 위한 선택이다.
//
// 걷는 방식은 패널이 실제로 하는 일과 **같게** 맞췄다:
//   · 패치는 병합이다 — StudioPro 의 onPatch 가 { ...form, ...patch } 하나뿐이다.
//     그래서 여기서도 withTypeDefaults 를 거치지 않는다. 거치면 그래프의 규칙 패치가 기본값을
//     빠뜨려도 시험이 메꿔 주어 아무것도 증명하지 못한다(그 대신 아래 "규칙 패치가 TYPE_DEFAULTS 를
//     그대로 담는다" 시험이 빠짐을 직접 잡는다).
//   · 건너뛰기 · 좁히기는 backend/app/coach_graph.py 의 next_key · _skip · patch_for 와 같다.
//   · 거래소는 두 갈래다 — 바이낸스(by)와 국내 현물(by_domestic). 국내는 서버가 규칙 K 와
//     일봉 아닌 적립식을 거절하므로 by 만 걸으면 그 길이 증명되지 않는다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  TYPE_DEFAULTS, buildMacro, defaultForm, validateDetailed, withExchangeDefaults, withTypeDefaults,
} from "../src/lib/macro.js";

const graph = JSON.parse(readFileSync(new URL("./fixtures/coachGraph.generated.json", import.meta.url), "utf8"));
const FILTERABLE = new Set(graph.filterable);

// 입력형 노드는 대표값 하나로 고정한다 — 선택형의 조합이 증명해야 할 것이다.
// 기간(period)도 고정한다: 아래 "기간 선택지는 preset 만 건드린다" 가 그래도 되는 근거를 댄다.
// 종목 수는 1~5 를 다 센다 — '첫 종목에 더 싣기' 의 비중 표와 자금을 나눌 몫이 종목 수마다 다르다.
const BINANCE_SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT"];
const DOMESTIC_SYMBOLS = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-XRP", "KRW-ADA"];
const CASES = [];
for (let n = 1; n <= 5; n += 1) {
  CASES.push({ name: `바이낸스 · 종목 ${n}개`, exchange: "binance", capital: "1000", symbols: BINANCE_SYMBOLS.slice(0, n).join(", ") });
  // 국내는 업비트 · 빗썸이 같은 길이다(isDomestic 하나로 갈린다) — 종목 수를 번갈아 두 거래소에 나눠 센다.
  CASES.push({ name: `국내 현물 · 종목 ${n}개`, exchange: n % 2 ? "upbit" : "bithumb", capital: "1000000", symbols: DOMESTIC_SYMBOLS.slice(0, n).join(", ") });
}

// 거래소를 고른 뒤의 출발 폼 — 조건 판이 거래소를 바꿀 때 하는 일(돈 칸 · 묶음 칸 비우기)을 그대로 거친다.
function baseForm(exchange) {
  return exchange === "binance" ? defaultForm() : withExchangeDefaults(defaultForm(), exchange, []);
}

const symbolCount = (answers) => {
  const raw = answers.symbols;
  if (typeof raw !== "string") return 0;
  return new Set(raw.split(",").map((s) => s.trim().toUpperCase()).filter(Boolean)).size;
};

// coach_graph.py 의 _skip 과 같다.
function skip(key, answers) {
  if (key === "weights") return symbolCount(answers) < 2;
  if (key === "entry_filter") return !FILTERABLE.has(answers.rule);
  if (key === "bundle_risk") return symbolCount(answers) < 2 || !FILTERABLE.has(answers.rule);
  return false;
}

// coach_graph.py 의 _narrow_rule · _narrow_weights 와 같다.
function choicesOf(key, node, answers, domestic) {
  if (key === "rule") {
    const table = domestic ? node.by_domestic : node.by;
    const list = table[`${answers.goal}|${answers.risk}|${answers.watch}`];
    assert.ok(Array.isArray(list) && list.length >= 3, `rule 후보가 없다: ${answers.goal}|${answers.risk}|${answers.watch}`);
    return list;
  }
  if (key === "weights") {
    const n = Math.min(Math.max(symbolCount(answers), 2), 5);
    return node.by[String(n)];
  }
  return node.choices || [];
}

const floor6 = (x) => Math.max(Math.floor(x * 1e6) / 1e6, 1e-6);

// 가장 작은 레그가 받는 자금의 몫 — coach_graph.py 의 _weights_share 와 같다. '첫 종목에 더 싣기' 의
// 비중은 그래프에서 읽는다(여기 적어 두면 한쪽이 낡는다).
function weightsShare(answers) {
  const n = symbolCount(answers);
  if (n < 2) return 1;
  let share = 1 / n;
  if (answers.weights === "lead") {
    const lead = (graph.nodes.weights.by[String(n)] || []).find((c) => c.value === "lead");
    const nums = String(lead?.patch?.leg_weights || "").split(",").map((s) => Number(s.trim())).filter((v) => v > 0);
    if (nums.length) share = Math.min(share, Math.min(...nums) / 100);
  }
  return share;
}

function investRatio(answers) {
  const pick = (graph.nodes.risk.choices || []).find((c) => c.value === answers.risk);
  return Number(pick?.patch?.invest_ratio_pct ?? 100) / 100;
}

// coach_graph.py 의 _capital_patch 와 같다 — 규칙 C · H 는 시작 자금에 맞춘 금액을 **함께** 낸다.
// 이 표(capital_scaled)대로 계산하지 않으면 H 가 서버 자금 검사를 못 넘기는 폼이 만들어져 시험이 거짓 실패한다.
function capitalPatch(answers, capital) {
  const out = { initial_capital: capital };
  const share = weightsShare(answers);
  const ratio = investRatio(answers);
  for (const row of graph.capital_scaled[answers.rule] || []) {
    out[row.key] = floor6(capital * share * (row.uses_invest_ratio ? ratio : 1) * row.coef);
  }
  return out;
}

/** 그래프를 걸어 (답 묶음, 폼) 쌍을 전부 만든다. */
function* walk(testCase) {
  const domestic = testCase.exchange !== "binance";
  const capital = Number(testCase.capital);
  const order = graph.order;

  function* step(i, answers, form) {
    if (i >= order.length) { yield { answers, form }; return; }
    const key = order[i];
    if (skip(key, answers)) { yield* step(i + 1, answers, form); return; }
    const node = graph.nodes[key];
    assert.ok(node, `그래프에 ${key} 노드가 없다`);

    if (node.kind === "symbols") {
      yield* step(i + 1, { ...answers, symbols: testCase.symbols },
        { ...form, [node.field]: testCase.symbols });
      return;
    }
    if (node.kind === "number") {
      yield* step(i + 1, { ...answers, capital: testCase.capital },
        { ...form, ...capitalPatch(answers, capital) });
      return;
    }
    if (node.kind === "period") {
      const first = node.choices[0];
      yield* step(i + 1, { ...answers, period: first.value }, { ...form, ...first.patch });
      return;
    }
    for (const choice of choicesOf(key, node, answers, domestic)) {
      yield* step(i + 1, { ...answers, [key]: choice.value }, { ...form, ...choice.patch });
    }
  }
  yield* step(0, {}, baseForm(testCase.exchange));
}

const where = (testCase, answers) => `${testCase.name} ${JSON.stringify(answers)}`;

for (const testCase of CASES) {
  test(`코치가 낼 수 있는 모든 폼이 검증을 통과한다 — ${testCase.name}`, () => {
    let n = 0;
    for (const { answers, form } of walk(testCase)) {
      const err = validateDetailed(form);
      assert.equal(err, null, `${where(testCase, answers)} -> ${JSON.stringify(err)}`);
      n += 1;
    }
    assert.ok(n > 100, `경로가 ${n}개뿐이다 — 그래프를 다 걷지 못했다`);
  });

  test(`모든 폼이 buildMacro 로 조립된다 — ${testCase.name}`, () => {
    let n = 0;
    for (const { answers, form } of walk(testCase)) {
      let macro;
      assert.doesNotThrow(() => { macro = buildMacro(form); }, where(testCase, answers));
      assert.ok(macro.rule_type, where(testCase, answers));
      assert.ok(macro.params && Object.keys(macro.params).length > 0, where(testCase, answers));
      // NaN 이 폼을 통과해 매크로에 들어가면 서버가 422 를 낸다 — 여기서 잡는다.
      for (const [k, v] of Object.entries(macro.params)) {
        assert.ok(!(typeof v === "number" && Number.isNaN(v)), `${k} 가 NaN (${where(testCase, answers)})`);
      }
      // 국내 현물은 롱 · 1배 · 현물만이다(서버가 그 밖을 거절한다).
      if (testCase.exchange !== "binance") {
        assert.equal(macro.position_side, "long", where(testCase, answers));
        assert.equal(macro.leverage, 1, where(testCase, answers));
        assert.notEqual(macro.market, "futures", where(testCase, answers));
        assert.notEqual(macro.rule_type, "K", where(testCase, answers));
      }
      n += 1;
    }
    assert.ok(n > 100, `경로가 ${n}개뿐이다`);
  });
}

// --- 그래프 자체의 모양 ------------------------------------------------

function* allChoices() {
  for (const [key, node] of Object.entries(graph.nodes)) {
    const lists = [node.choices || []];
    if (node.by) lists.push(...Object.values(node.by));
    if (node.by_domestic) lists.push(...Object.values(node.by_domestic));
    for (const list of lists) for (const choice of list) yield { key, choice };
  }
}

test("그래프의 모든 패치 키가 폼에 있는 키다", () => {
  const known = new Set(Object.keys(defaultForm()));
  let n = 0;
  for (const { key, choice } of allChoices()) {
    for (const k of Object.keys(choice.patch || {})) {
      assert.ok(known.has(k), `폼에 없는 키: ${k} (${key}/${choice.value})`);
      n += 1;
    }
  }
  assert.ok(n > 100, `패치 키가 ${n}개뿐이다 — 그래프를 다 읽지 못했다`);
});

test("자금에 맞춰 바뀌는 칸도 폼에 있는 키다", () => {
  const known = new Set(Object.keys(defaultForm()));
  for (const [rule, rows] of Object.entries(graph.capital_scaled)) {
    for (const row of rows) assert.ok(known.has(row.key), `폼에 없는 키: ${row.key} (capital_scaled/${rule})`);
    assert.ok(rows.every((row) => row.coef > 0), `${rule} 의 계수가 0 이하다`);
  }
});

test("규칙 패치가 macro.js 의 TYPE_DEFAULTS 를 그대로 담는다", () => {
  // 전수 시험이 withTypeDefaults 를 거치지 않으므로(패널도 거치지 않는다) 규칙 패치가 그 규칙의
  // 기본값을 **스스로** 담아야 한다. 안 담으면 이전 규칙의 값이 남는다.
  const seen = new Set();
  for (const { key, choice } of allChoices()) {
    if (key !== "rule" || seen.has(choice.value)) continue;
    seen.add(choice.value);
    const rule = choice.value;
    assert.equal(choice.patch.rule_type, rule);
    for (const [k, v] of Object.entries(TYPE_DEFAULTS[rule] || {})) {
      assert.equal(choice.patch[k], v, `규칙 ${rule} 의 기본값 ${k} 가 패치에 없거나 다르다`);
    }
    // withTypeDefaults 가 바꿀 칸(숏 금지 · C 의 1배 · 필터 못 쓰는 규칙)도 패치가 스스로 담는다.
    const wanted = withTypeDefaults(defaultForm(), rule);
    for (const k of ["position_side", "leverage"]) {
      assert.equal(choice.patch[k], wanted[k], `규칙 ${rule} 의 ${k} 가 패치에 없거나 다르다`);
    }
    if (!FILTERABLE.has(rule)) {
      assert.equal(choice.patch.use_entry_filter, false, `규칙 ${rule} 은 진입 조건을 꺼야 한다`);
      assert.equal(choice.patch.use_bundle_risk, false, `규칙 ${rule} 은 묶음 한도를 꺼야 한다`);
    }
  }
  assert.ok(seen.size >= 7, `규칙이 ${seen.size}개뿐이다`);
});

test("기간 선택지는 preset 만 건드린다", () => {
  // 전수 걷기가 기간을 대표값 하나로 고정하는 근거 — 기간은 다른 칸을 건드리지 않고
  // 검증도 보지 않으므로, 다섯 갈래로 불릴 이유가 없다.
  const presets = new Set(["1y", "6m", "3m", "1m", "1w", "1d"]);
  for (const choice of graph.nodes.period.choices) {
    assert.deepEqual(Object.keys(choice.patch), ["preset"]);
    assert.ok(presets.has(choice.patch.preset), `모르는 기간: ${choice.patch.preset}`);
    assert.notEqual(choice.patch.preset, "custom", "직접 지정은 시작 · 종료 날짜가 필요하다");
  }
});

test("국내 현물 후보에는 금지된 규칙이 없다", () => {
  const banned = new Set(graph.domestic_banned_rules);
  assert.ok(banned.size > 0, "국내 금지 규칙 목록이 비었다");
  for (const [combo, list] of Object.entries(graph.nodes.rule.by_domestic)) {
    for (const choice of list) assert.ok(!banned.has(choice.value), `${combo} 에 ${choice.value} 가 남았다`);
    assert.ok(list.length >= 3, `${combo} 의 후보가 ${list.length}개뿐이다`);
  }
});
