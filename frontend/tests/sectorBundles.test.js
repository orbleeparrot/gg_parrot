// 분야 묶음 — 묶음을 분야로 나눠 보여 주고, 묶음의 운용기간은 3개월부터 (2026-11-02).
//
// 설계: docs/superpowers/specs/2026-11-02-sector-bundles-design.md
import test from "node:test";
import assert from "node:assert/strict";
import { renderComponent, textOf } from "./renderHelper.js";
import { bundleStatPhrases, splitBundles } from "../src/lib/portfolio.js";
import { BUNDLE_HORIZONS, HORIZONS } from "../src/lib/askCopy.js";
import { answerLabel, horizonsFor, initialState, reduce } from "../src/lib/askFlow.js";

const leg = (symbol) => ({ symbol, weight: 25 });
const SECTOR = {
  key: "sector_layer1", name: "레이어1", kind: "sector", count: 4,
  lead: "자체 블록체인을 가진 코인들이에요.",
  tags: ["분야"], stats: { rank_min: 2, rank_max: 14, range_avg_pct: 6.2 },
  legs: [leg("KRW-BTC"), leg("KRW-ETH"), leg("KRW-SOL"), leg("KRW-ADA")],
};
const SIZE = {
  key: "cap_top5", name: "시가총액 TOP5", kind: "size", count: 2,
  lead: "시가총액 상위로 나눠 담아요.", tags: ["메이저"], stats: null,
  legs: [leg("KRW-BTC"), leg("KRW-ETH")],
};

// --- 두 줄로 가르기 ----------------------------------------------------------

test("분야 묶음과 기준 묶음이 갈린다", () => {
  const { sectors, sizes } = splitBundles([SIZE, SECTOR]);
  assert.deepEqual(sectors.map((i) => i.key), ["sector_layer1"]);
  assert.deepEqual(sizes.map((i) => i.key), ["cap_top5"]);
});

test("kind 를 모르는 옛 응답은 기준 묶음으로 둔다", () => {
  // 분야 줄에 올리면 분야가 아닌 것이 분야 자리에 앉는다. 모를 때는 원래 자리가 맞다.
  const { sectors, sizes } = splitBundles([{ key: "cap_top10", legs: [] }]);
  assert.equal(sectors.length, 0);
  assert.deepEqual(sizes.map((i) => i.key), ["cap_top10"]);
});

test("목록이 없어도 깨지지 않는다", () => {
  assert.deepEqual(splitBundles(null), { sectors: [], sizes: [] });
});

// --- 묶음에 붙는 숫자 두 칸 ---------------------------------------------------

test("거래대금 순위는 폭으로, 하루 변동은 평균 한 값으로 적는다", () => {
  assert.deepEqual(bundleStatPhrases(SECTOR.stats), { rank: "2~14위", range: "6.2%" });
});

test("레그가 모두 같은 순위면 폭 대신 한 값만 적는다", () => {
  assert.equal(bundleStatPhrases({ rank_min: 3, rank_max: 3, range_avg_pct: 1 }).rank, "3위");
});

test("서버가 숫자를 못 구했으면 아무 칸도 만들지 않는다", () => {
  // 0 을 적으면 화면이 그걸 '거래가 없다' 로 읽는다.
  assert.equal(bundleStatPhrases(null), null);
  assert.equal(bundleStatPhrases({ rank_min: 1, rank_max: null, range_avg_pct: 2 }), null);
});

// --- 묶음의 운용기간 ----------------------------------------------------------

test("묶음은 3개월 · 6개월 · 1년 셋만 고른다", () => {
  assert.deepEqual(BUNDLE_HORIZONS.map((h) => h.label), ["3개월", "6개월", "1년"]);
  assert.equal(BUNDLE_HORIZONS.some((h) => h.value === "days"), false, "1주는 묶음에 쓸 수 없다");
});

test("넘기는 값은 안 바뀐다 — 서버는 그대로다", () => {
  assert.deepEqual(BUNDLE_HORIZONS.map((h) => h.value), ["weeks", "months", "long"]);
  for (const h of BUNDLE_HORIZONS) {
    assert.ok(HORIZONS.some((one) => one.value === h.value), `${h.value} 는 서버가 아는 값이어야 한다`);
  }
});

test("종목 하나 쪽 기간은 한 글자도 안 바뀐다", () => {
  assert.deepEqual(HORIZONS.map((h) => h.label), ["며칠", "몇 주", "몇 달", "길게"]);
  assert.equal(horizonsFor({ mode: "single" }).length, 4);
  assert.equal(horizonsFor({ mode: "bundle" }).length, 3);
});

function upTo(step, mode) {
  let s = initialState();
  s = reduce(s, { type: "choose", step: "exchange", value: "binance" });
  s = reduce(s, { type: "choose", step: "mode", value: mode });
  s = reduce(s, { type: "choose", step: "balance", value: 250 });
  s = reduce(s, { type: "choose", step: "profile", value: "balanced" });
  s = reduce(s, { type: "choose", step: "market", value: { market: "spot", leverage: 1 } });
  return step === "horizon" ? s : s;
}

test("묶음에서 1주를 고르면 받아 주지 않는다", () => {
  const before = upTo("horizon", "bundle");
  const after = reduce(before, { type: "choose", step: "horizon", value: "days" });
  assert.equal(after.answers.horizon, null);
  assert.equal(after.step, "horizon", "같은 질문에 그대로 머문다");
});

test("묶음에서 3개월은 받아 준다", () => {
  const after = reduce(upTo("horizon", "bundle"), { type: "choose", step: "horizon", value: "weeks" });
  assert.equal(after.answers.horizon, "weeks");
});

test("종목 하나에서는 1주를 그대로 고른다", () => {
  const after = reduce(upTo("horizon", "single"), { type: "choose", step: "horizon", value: "days" });
  assert.equal(after.answers.horizon, "days");
});

test("되돌아본 답에도 묶음은 3개월이라 적힌다", () => {
  const bundle = reduce(upTo("horizon", "bundle"), { type: "choose", step: "horizon", value: "weeks" });
  assert.equal(answerLabel("horizon", bundle.answers), "3개월");
  const single = reduce(upTo("horizon", "single"), { type: "choose", step: "horizon", value: "weeks" });
  assert.equal(answerLabel("horizon", single.answers), "몇 주");
});

// --- 물어볼까의 묶음 카드 -----------------------------------------------------

const renderCards = (props) => renderComponent("src/components/BundleCards.jsx",
  { items: [SECTOR], marketText: "업비트 · 현물", picked: [], onPick: () => {}, ...props });

test("묶음 카드가 담길 종목을 다 보여 준다", async () => {
  // 누르기 전에 무엇이 담기는지 보여야 한다 — 값이 오를지 내릴지는 말하지 않는 제품이라
  // 고르기 전에 쥘 수 있는 정보가 그것뿐이다.
  const text = textOf(await renderCards());
  for (const base of ["BTC", "ETH", "SOL", "ADA"]) assert.match(text, new RegExp(base));
  assert.match(text, /레이어1/);
  assert.match(text, /4\s*종목/);
});

test("묶음 카드에 숫자 두 칸이 붙는다", async () => {
  const text = textOf(await renderCards());
  assert.match(text, /2~14위/);
  assert.match(text, /6\.2%/);
});

test("숫자를 못 구한 묶음은 그 칸을 안 그린다", async () => {
  const text = textOf(await renderCards({ items: [{ ...SECTOR, stats: null }] }));
  assert.match(text, /레이어1/);
  assert.doesNotMatch(text, /거래대금 순위/);
});

test("분야를 못 받으면 까닭을 한 줄 적는다", async () => {
  const text = textOf(await renderCards({ items: [] }));
  assert.match(text, /분야/);
  assert.ok(text.length > 0, "빈 자리를 말없이 두지 않는다");
});

test("묶음 카드는 '추천' 이라 쓰지 않는다", async () => {
  // 설계 그대로다 — 어디에도 '추천' 이라 쓰지 않는다(ask_candidates 머리글).
  assert.doesNotMatch(textOf(await renderCards()), /추천/);
});

// --- 빌더의 두 줄 -------------------------------------------------------------

const renderChips = (items) => renderComponent("src/components/BundleChips.jsx",
  { items, onPick: () => {} });

test("빌더는 추천 묶음과 분야 묶음을 두 줄로 그린다", async () => {
  const text = textOf(await renderChips([SIZE, SECTOR]));
  assert.match(text, /추천 묶음/);
  assert.match(text, /분야 묶음/);
  assert.match(text, /시가총액 TOP5/);
  assert.match(text, /레이어1/);
});

test("한쪽이 비면 그 줄은 아예 안 그린다", async () => {
  const onlySectors = textOf(await renderChips([SECTOR]));
  assert.doesNotMatch(onlySectors, /추천 묶음/);
  assert.match(onlySectors, /분야 묶음/);
});

test("묶음을 하나도 못 받으면 줄이 없다", async () => {
  assert.equal(textOf(await renderChips([])), "");
});
