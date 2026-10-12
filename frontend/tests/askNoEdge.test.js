// 물어볼까 — "그냥 들고 있는 게 나았다" 면 추천하지 않고 그렇게 말한다 (2026-10-08).
//
// 서버가 홀딩을 문턱(ask.MIN_EXCESS_PCT)만큼 못 넘긴 조합을 아예 내보내지 않으므로, 결과가
// 비는 이유가 둘이 되었다: 돌렸는데 넘은 게 없음(no_edge)과 애초에 살아남은 후보가 없음.
// 화면이 하는 말이 달라야 한다.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { noEdgeDetail, resultsHeadline } from "../src/lib/askView.js";
import { HOLD_LABEL, NO_EDGE_NOTE, NO_EDGE_TEXT, NO_RESULTS_TEXT, REFUNDED_TEXT } from "../src/lib/askCopy.js";
import { initialState, reduce } from "../src/lib/askFlow.js";

const dialog = readFileSync(new URL("../src/components/AskParrotDialog.jsx", import.meta.url), "utf8");

test("빈 결과의 두 이유를 다르게 말한다", () => {
  assert.equal(resultsHeadline(0, true), NO_EDGE_TEXT, "돌렸는데 넘은 게 없음");
  assert.equal(resultsHeadline(0, false), NO_RESULTS_TEXT, "살아남은 후보가 없음");
  assert.notEqual(NO_EDGE_TEXT, NO_RESULTS_TEXT, "두 문구가 같으면 구분하는 뜻이 없다");
  // 기본값은 예전 동작이어야 한다 — 두 번째 인자를 안 넘기는 곳이 있으면 거기서 뜻이 바뀌면 안 된다.
  assert.equal(resultsHeadline(0), NO_RESULTS_TEXT);
});

test("결과가 있으면 못 넘겼다는 말은 하지 않는다", () => {
  assert.notEqual(resultsHeadline(1, true), NO_EDGE_TEXT, "한 개라도 올라왔으면 넘긴 것이다");
  assert.notEqual(resultsHeadline(3, true), NO_EDGE_TEXT);
});

test("흐름 상태가 no_edge 를 싣고 다닌다", () => {
  const start = initialState();
  assert.equal(start.noEdge, false);
  const empty = reduce(start, { type: "results", results: [], noEdge: true, remaining: 4 });
  assert.equal(empty.phase, "results");
  assert.deepEqual(empty.results, []);
  assert.equal(empty.noEdge, true);
  // 추천이 올라온 경우엔 꺼져 있어야 한다.
  const some = reduce(start, { type: "results", results: [{ rule_type: "J" }], remaining: 4 });
  assert.equal(some.noEdge, false);
});

test("화면이 서버의 no_edge 를 받아 안내로 바꾼다", () => {
  assert.match(dialog, /noEdge: data\.no_edge/, "서버 칸을 그대로 받아야 한다");
  assert.doesNotMatch(dialog, /all_lost_to_hold/, "없어진 칸을 읽으면 늘 undefined 다");
  assert.match(dialog, /state\.noEdge \?.*NO_EDGE_NOTE/s, "못 넘겼을 때 이유를 적어 준다");
  assert.match(dialog, /resultsHeadline\(results\.length, state\.noEdge\)/, "머리글도 이유를 봐야 한다");
  assert.ok(NO_EDGE_NOTE.includes("다시"), "다음에 뭘 하면 되는지 말해야 한다");
});

// 결과가 빈 경우가 이제 실제로 생긴다. `{results.length && <X/>}` 는 0 일 때 React 가
// **숫자 0 을 글자로 그린다** — 추천이 없을 때 화면에 "0" 두 개가 뜨던 자리다.
test("빈 결과에서 0 이 글자로 새지 않는다", () => {
  const bare = [...dialog.matchAll(/\{\s*results\.length\s*&&/g)];
  assert.deepEqual(bare.map((m) => m[0]), [],
    "results.length 를 그대로 && 왼쪽에 두면 0 이 화면에 찍힌다 — results.length > 0 으로");
});

// --- 횟수 환불 (2026-10-08) -----------------------------------------------
// 하루 횟수는 백테스트 전에 깎여서, 결과 0개를 받아도 1회가 사라졌다.
// 짧은 봉 사전 안내는 2026-10-12 에 성향 단계에서 빈도 단계로 옮겼다(askProfiles.test.js).

test("흐름 상태가 환불 여부를 싣고 다닌다", () => {
  const start = initialState();
  assert.equal(start.refunded, false);
  const empty = reduce(start, { type: "results", results: [], noEdge: true, refunded: true, remaining: 5 });
  assert.equal(empty.refunded, true);
  const some = reduce(start, { type: "results", results: [{ rule_type: "J" }], remaining: 4 });
  assert.equal(some.refunded, false, "추천이 나왔으면 횟수를 쓴 것이다");
});

test("화면이 환불을 받아 말한다", () => {
  assert.match(dialog, /refunded: data\.refunded/, "서버 칸을 그대로 받아야 한다");
  assert.match(dialog, /state\.refunded \?.*REFUNDED_TEXT/s, "돌려줬음을 말해야 한다");
  assert.ok(REFUNDED_TEXT.includes("횟수"), "무엇을 돌려줬는지 말해야 한다");
});

test("빈 화면이 숫자를 싣는다", () => {
  // 한 줄로 끝나면 매크로가 벌었던 사실이 사라진다.
  const line = noEdgeDetail({ return_pct: 34.82, hold_return_pct: 38.67, rule_label: "트레일링 스탑" });
  assert.match(line, /34\.8/, "매크로가 얼마 벌었는지");
  assert.match(line, /38\.7/, "같은 기간 보유는 얼마였는지");
  assert.match(line, /첫날/, "그 기준이 첫날 매수라는 전제를 드러내야 한다");
});

test("없는 홀딩 숫자를 지어내지 않는다", () => {
  const line = noEdgeDetail({ return_pct: 5.0, hold_return_pct: null, rule_label: "RSI 조건" });
  assert.match(line, /5\.0/);
  assert.ok(!/null|NaN/.test(line), `지어낸 값이 섞였다: ${line}`);
  // signedPct(null) 은 "—" 를 돌려준다. 그 자리를 비워 둔 문장("… 뒀을 때 — 였어요")은
  // 숫자를 지어내지 않지만 고장 난 화면처럼 읽힌다 — 못 구했다고 **말해야** 한다.
  assert.ok(!line.includes("—"), `빈 자리를 그대로 내보냈다: ${line}`);
  assert.match(line, /구하지 못했어요/, "왜 홀딩 숫자가 없는지 말해야 한다");
  assert.equal(noEdgeDetail(null), "");
});

test("홀딩 라벨이 전제를 드러낸다", () => {
  // '그냥 들고 있기' 는 매수일이 공짜로 주어진 것을 숨긴다 — 구간 첫 봉이 그 날이다.
  assert.match(HOLD_LABEL, /첫날/);
  assert.ok(!NO_EDGE_TEXT.includes("그냥 들고 있는 게 나았"),
    "매수지점도 같은 기준으로 정해진 날이라 '들고 있는 게 나았다' 는 헷갈린다");
});

test("실행 화면이 서버의 빈 화면 숫자를 받아 그린다", () => {
  // 안 받으면 숫자는 서버에만 있고 사용자는 한 줄만 본다 — 바로 그 문제였다.
  assert.match(dialog, /noEdgeBest: data\.no_edge_best/, "응답 칸을 그대로 받아야 한다");
  assert.match(dialog, /noEdgeDetail\(state\.noEdgeBest\)/, "빈 화면에 그려야 한다");
});
