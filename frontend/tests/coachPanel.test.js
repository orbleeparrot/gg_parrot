// 코치 패널 — SSR 한 번만 그리므로 클릭 흐름은 증명하지 못한다. 그래서 두 가지를 한다:
//  1) initialState 로 상태를 넣어 "그려지는 것"을 본다.
//  2) 손으로 쓴 질문만 믿지 않는다 — 서버 그래프를 내보낸 자료(fixtures/coachGraph.generated.json)의 실제 질문으로도 그리고,
//     api.js · CoachPanel.jsx 소스를 읽어 끼운 자리를 단정한다(그릴 대상을 잘못 고른 시험이 수트를 통과시킨 적이 있다).
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";

const read = (rel) => readFileSync(new URL(rel, import.meta.url), "utf8");
const GRAPH = JSON.parse(read("./fixtures/coachGraph.generated.json"));

const QUESTION = {
  key: "goal", ask: "무엇을 하고 싶어요?", kind: "choice", has_more: true, field: "",
  choices: [
    { value: "steady", label: "꾸준히 조금씩", why: "매수 시점을 안 고민해요", patch: {} },
    { value: "trend", label: "추세를 타고 싶어요", why: "", patch: {} },
    { value: "dip", label: "흔들릴 때 사고 싶어요", why: "", patch: {} },
  ],
};
const BASE = { turn: 1, max_turns: 14, remaining_today: 4, turns: [] };

function render(state, props = {}) {
  return renderComponent("src/components/CoachPanel.jsx",
    { onPatch: () => {}, onDone: () => {}, initialState: state, ...props });
}

// 서버가 한 질문을 보내는 모양(coach._question_view)을 그래프 자료에서 만든다. 첫 페이지만 담는다.
function serverQuestion(key) {
  const node = GRAPH.nodes[key];
  const shown = node.choices.slice(0, GRAPH.page_size);
  return {
    key, ask: node.ask, kind: node.kind, field: node.field, choices: shown,
    has_more: node.choices.length > GRAPH.page_size,
  };
}

test("시작 전에는 안내와 시작하기만 보인다", async () => {
  const text = textOf(await render(null));
  assert.match(text, /무엇을 만들까요\? 몇 가지만 물어보고 판을 채워 드려요\./);
  assert.match(text, /시작하기/);
  assert.match(text, /껄무새 코치/);
  assert.doesNotMatch(text, /다른 선택지 보기/);
  assert.doesNotMatch(text, /남은 횟수/);
});

test("질문과 선택지를 그린다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION }));
  assert.match(text, /무엇을 하고 싶어요\?/);
  assert.match(text, /꾸준히 조금씩/);
  assert.match(text, /추세를 타고 싶어요/);
  assert.doesNotMatch(text, /시작하기/);
});

test("근거가 있으면 보여 준다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION }));
  assert.match(text, /매수 시점을 안 고민해요/);
});

test("has_more 면 다른 선택지 보기가 있다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION }));
  assert.match(text, /다른 선택지 보기/);
});

test("has_more 가 아니면 다른 선택지 보기가 없다", async () => {
  const text = textOf(await render({ ...BASE, question: { ...QUESTION, has_more: false } }));
  assert.doesNotMatch(text, /다른 선택지 보기/);
});

test("남은 횟수와 턴을 보여 준다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION, turn: 4, remaining_today: 2 }));
  assert.match(text, /남은 횟수 2회/);
  assert.match(text, /4\s*\/\s*14/);
});

test("start 직후 턴은 0 이다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION, turn: 0 }));
  assert.match(text, /0\s*\/\s*14/);
});

test("지난 턴을 기록으로 보여 주고 되돌아갈 수 있다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION, turn: 2, turns: [{ key: "goal", label: "꾸준히 조금씩" }] }));
  assert.match(text, /꾸준히 조금씩/);
  assert.match(text, /한 단계 되돌리기/);
});

test("기록이 없으면 되돌리기도 없다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION, turn: 0 }));
  assert.doesNotMatch(text, /되돌리기/);
});

test("숫자 질문은 선택지 대신 입력칸을 그린다", async () => {
  const q = { key: "capital", ask: "얼마로 시작할까요?", kind: "number", choices: [], has_more: false, field: "initial_capital" };
  const html = await render({ ...BASE, question: q, turn: 8 });
  assert.match(html, /<input/);
  assert.match(textOf(html), /얼마로 시작할까요\?/);
  assert.doesNotMatch(html, /coach-choice/);
});

test("마무리되면 끝났다고 말한다", async () => {
  const text = textOf(await render({ ...BASE, question: null, done: true, wrapped_up: true, turn: 14 }));
  assert.match(text, /여기까지 정했어요 · 나머지는 기본값으로 뒀으니 판에서 고쳐요/);
});

test("스스로 끝난 대화는 마무리 문구를 쓰지 않는다", async () => {
  const text = textOf(await render({ ...BASE, question: null, done: true, wrapped_up: false, turn: 10 }));
  assert.match(text, /다 정했어요/);
  assert.doesNotMatch(text, /나머지는 기본값으로/);
});

test("끝나면 선택지가 없다", async () => {
  const text = textOf(await render({ ...BASE, question: null, done: true, turn: 10 }));
  assert.doesNotMatch(text, /다른 선택지 보기/);
});

test("한도가 0이고 세션이 없으면 다 썼다고 말한다", async () => {
  const text = textOf(await render({ question: null, remaining_today: 0, turn: 0, max_turns: 14, turns: [] }));
  assert.match(text, /오늘은 다 썼어요 · 내일 다시 와 주세요/);
});

test("한도가 0이어도 진행 중인 질문은 그대로 보인다", async () => {
  const text = textOf(await render({ ...BASE, question: QUESTION, remaining_today: 0 }));
  assert.match(text, /무엇을 하고 싶어요\?/);
  assert.doesNotMatch(text, /다 썼어요/);
});

test("서버가 거절한 말을 그대로 보여 준다", async () => {
  const html = await render({ ...BASE, question: QUESTION, error: "고를 수 없는 답이에요: x" });
  assert.match(html, /role="alert"/);
  assert.match(textOf(html), /고를 수 없는 답이에요: x/);
});

// --- 실제 그래프로 그린다 -------------------------------------------------
test("그래프의 모든 질문을 그린다 — 입력형은 number · symbols 뿐이고 나머지(period 포함)는 선택지다", async () => {
  const kinds = new Set(Object.values(GRAPH.nodes).map((n) => n.kind));
  assert.deepEqual([...kinds].sort(), ["choice", "number", "period", "symbols"]);
  for (const key of Object.keys(GRAPH.nodes)) {
    const q = serverQuestion(key);
    const html = await render({ ...BASE, question: q });
    const text = textOf(html);
    assert.match(text, new RegExp(q.ask.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `${key}: 질문 문구`);
    if (q.kind === "number" || q.kind === "symbols") {
      assert.match(html, /<input/, `${key}: 입력칸`);
      assert.doesNotMatch(html, /coach-choice/, `${key}: 입력형에 선택지 버튼 없음`);
    } else {
      assert.doesNotMatch(html, /<input/, `${key}: 선택형에 입력칸 없음`);
      assert.ok(q.choices.length > 0, `${key}: 선택지가 있다`);
      for (const choice of q.choices) {
        assert.ok(text.includes(choice.label), `${key}: 서버 라벨 ${choice.label}`);
        if (choice.why) assert.ok(text.includes(choice.why), `${key}: 서버 근거 ${choice.why}`);
      }
    }
    assert.equal(/다른 선택지 보기/.test(text), q.kind !== "number" && q.kind !== "symbols" && q.has_more, `${key}: has_more 와 일치`);
  }
});

test("period 질문은 선택지로 그려진다 — 입력칸이 아니다", async () => {
  const html = await render({ ...BASE, question: serverQuestion("period") });
  assert.doesNotMatch(html, /<input/);
  assert.match(html, /coach-choice/);
});

test("종목 질문의 입력칸 안내는 거래소를 따른다", async () => {
  const q = serverQuestion("symbols");
  const binance = await render({ ...BASE, question: q }, { exchange: "binance" });
  assert.match(binance, /placeholder="BTCUSDT, ETHUSDT"/);
  for (const exchange of ["upbit", "bithumb"]) {
    const html = await render({ ...BASE, question: q }, { exchange });
    assert.match(html, /placeholder="KRW-BTC, KRW-ETH"/, exchange);
  }
  // 거래소 값이 이상해도 그리다 죽지 않는다.
  assert.match(await render({ ...BASE, question: q }, { exchange: "nope" }), /placeholder="BTCUSDT, ETHUSDT"/);
});

test("숫자 질문에는 종목 안내가 붙지 않는다", async () => {
  const html = await render({ ...BASE, question: serverQuestion("capital") });
  assert.doesNotMatch(html, /placeholder="(BTCUSDT|KRW-BTC)/);
});

// --- 소스를 읽어 단정한다 -------------------------------------------------
test("api.js 에 코치 함수 네 개가 평평한 이름으로 있고 알맞은 끝점을 부른다", () => {
  const src = read("../src/api.js");
  const want = { coachStart: "start", coachAnswer: "answer", coachMore: "more", coachBack: "back" };
  for (const [name, route] of Object.entries(want)) {
    const line = src.split("\n").find((l) => l.trimStart().startsWith(`${name}:`));
    assert.ok(line, `${name} 이 api 객체에 있다`);
    assert.ok(line.includes(`"/api/coach/${route}"`), `${name} → /api/coach/${route}`);
    assert.ok(line.includes('method: "POST"'), `${name} 은 POST`);
  }
  // ask 묶음처럼 평평한 이름이다 — api.coach.start 같은 중첩 모양을 만들지 않는다.
  assert.doesNotMatch(src, /^\s*coach:\s*[{(]/m);
});

test("패널은 api.js 의 그 네 함수를 부른다", () => {
  const src = read("../src/components/CoachPanel.jsx");
  for (const name of ["coachStart", "coachAnswer", "coachMore", "coachBack"]) {
    assert.ok(new RegExp(`api\\.${name}\\(`).test(src), `${name} 호출`);
  }
  assert.doesNotMatch(src, /api\.coach\./);
});

test("패널은 Builder · SymbolPicker 에 기대지 않는다", () => {
  const src = read("../src/components/CoachPanel.jsx");
  assert.doesNotMatch(src, /^import .*Builder/m);
  assert.doesNotMatch(src, /^import .*SymbolPicker/m);
});

test("패널 소스에 선택지 라벨 사전이 없다 — 라벨은 서버가 보낸 것만 쓴다", () => {
  const src = read("../src/components/CoachPanel.jsx");
  const labels = new Set();
  for (const node of Object.values(GRAPH.nodes)) {
    for (const choice of node.choices) {
      labels.add(choice.label);
      if (choice.why) labels.add(choice.why);
    }
    labels.add(node.ask);
  }
  assert.ok(labels.size > 20, "그래프에서 문구를 읽었다");
  for (const label of labels) assert.ok(!src.includes(label), `패널 소스에 서버 문구가 박혀 있다: ${label}`);
});
