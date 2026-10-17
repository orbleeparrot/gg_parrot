import assert from "node:assert/strict";
import test from "node:test";

import {
  formatPct, formatPrice, formatTurnover, matchScore, mergeById, neighborsByTurnover, premiumPct,
  raceRows, rangePosition, refreshFirstPage, relativeTime, searchCoins, sortCoins, thumbGeometry, turnoverRank,
} from "../src/features/coinTrends/trendsFormat.js";

const coin = (base, extra = {}) => ({ base, symbol: `KRW-${base}`, korean_name: base, english_name: base,
  last_price: 1000, change_pct: 0, quote_volume: 100, ...extra });

test("prices follow KRW tick digits and USDT significant digits", () => {
  assert.equal(formatPrice(109_922_000, "KRW"), "109,922,000");
  assert.equal(formatPrice(56.6, "KRW"), "56.60");
  assert.equal(formatPrice(5.87, "KRW"), "5.870");
  assert.equal(formatPrice(0.93, "KRW"), "0.9300");
  assert.equal(formatPrice(81608.45, "USDT"), "81,608.45");
  assert.equal(formatPrice(0.004642, "USDT"), "0.004642");
  // 아주 싼 코인도 지수 표기(6.123e-7) 없이 유효 숫자 4자리.
  assert.equal(formatPrice(6.123e-7, "USDT"), "0.0000006123");
  assert.equal(formatPrice(1.5, "USDT"), "1.5");
  assert.equal(formatPrice(null, "KRW"), "—");
});

test("turnover uses 억·조 for KRW and B·M for USDT", () => {
  assert.equal(formatTurnover(85_000_000_000, "KRW"), "850억");
  assert.equal(formatTurnover(940_000_000, "KRW"), "9.4억");
  assert.equal(formatTurnover(1.23e12, "KRW"), "1.23조");
  assert.equal(formatTurnover(3_510_000_000, "USDT"), "$3.51B");
  assert.equal(formatTurnover(44_524_596, "USDT"), "$44.5M");
  assert.equal(formatPct(-0.392), "-0.39%");
  assert.equal(formatPct(0), "0.00%");
});

test("sorting keeps turnover as the tie-breaker and mentions drop silent coins", () => {
  const coins = [coin("BTC", { change_pct: 2, quote_volume: 900 }), coin("ETH", { change_pct: -3, quote_volume: 500 }),
    coin("XRP", { change_pct: 2, quote_volume: 950 })];
  assert.deepEqual(sortCoins(coins, "turnover").map((c) => c.base), ["XRP", "BTC", "ETH"]);
  assert.deepEqual(sortCoins(coins, "gainers").map((c) => c.base), ["XRP", "BTC", "ETH"]);
  assert.deepEqual(sortCoins(coins, "losers").map((c) => c.base), ["ETH", "XRP", "BTC"]);
  assert.deepEqual(sortCoins(coins, "mentions", { ETH: 3, BTC: 7 }).map((c) => c.base), ["BTC", "ETH"]);
  assert.equal(turnoverRank(coins, "ETH"), 3);
});

test("race rows keep the server order and scale bars to the leader", () => {
  const coins = [coin("DKA", { change_pct: 10 }), coin("GTC", { change_pct: 5 }), coin("OLD", { change_pct: -1 })];
  const rows = raceRows(coins, ["DKA", "GTC", "OLD", "MISSING"]);
  assert.deepEqual(rows.map((row) => [row.coin.base, row.pos]), [["DKA", 1], ["GTC", 0.5]]);
});

test("search matches aliases in brackets before longer names and keeps short tickers exact", () => {
  const upbit = [coin("XRP", { korean_name: "엑스알피(리플)", quote_volume: 850 }),
    coin("RLUSD", { korean_name: "리플유에스디", quote_volume: 1 }), coin("T", { korean_name: "쓰레스홀드" })];
  assert.equal(matchScore(upbit[0], "리플"), 1);
  assert.ok(matchScore(upbit[1], "리플") > 1);
  assert.equal(matchScore(upbit[2], "t"), 0);
  assert.equal(matchScore(coin("TAO", { english_name: "Bittensor" }), "t"), -1);
  const bithumbOnly = coin("EGG", { korean_name: "네스트리" });
  const result = searchCoins("네스", { exchange: "upbit", lists: { upbit, bithumb: [bithumbOnly] } });
  assert.deepEqual(result.here, []);
  assert.deepEqual(result.other.map((hit) => [hit.exchange, hit.coin.base]), [["bithumb", "EGG"]]);
  assert.deepEqual(searchCoins("리플", { exchange: "upbit", lists: { upbit } }).here.map((c) => c.base), ["XRP", "RLUSD"]);
});

test("neighbors stay inside the list at both ends", () => {
  const coins = ["A", "B", "C", "D", "E", "F"].map((base, index) => coin(base, { quote_volume: 600 - index }));
  assert.deepEqual(neighborsByTurnover(coins, "A").map((n) => n.rank), [1, 2, 3, 4, 5]);
  assert.deepEqual(neighborsByTurnover(coins, "D").map((n) => n.coin.base), ["B", "C", "D", "E", "F"]);
  assert.deepEqual(neighborsByTurnover(coins, "ZZZ"), []);
});

test("premium, range and relative time", () => {
  assert.equal(Math.round(premiumPct(109_922_000, 81_608.45, 1334.89) * 100) / 100, 0.9);
  assert.equal(premiumPct(100, 0, 1300), null);
  assert.equal(rangePosition(1766, 1759, 1773), 0.5);
  assert.equal(rangePosition(10, 10, 10), null);
  const now = Date.parse("2026-10-17T03:51:00Z");
  assert.equal(relativeTime("2026-10-17T03:45:00Z", { now }), "6분 전");
  assert.equal(relativeTime("2026-10-17T01:00:00Z", { now }), "2시간 전");
  assert.equal(relativeTime("2026-10-14", { now }), "10월 14일");
  assert.equal(relativeTime("2026-10-17T03:50:30Z", { now }), "방금");
});

test("merging pages never repeats a post", () => {
  const merged = mergeById([{ id: "a" }, { id: "b" }], [{ id: "b" }, { id: "c" }]);
  assert.deepEqual(merged.map((item) => item.id), ["a", "b", "c"]);
});

test("other-exchange search hits are ranked by match, not by exchange order", () => {
  // 빗썸의 약한 영문명 일치 둘이 바이낸스의 정확한 티커를 밀어내지 않는다.
  const lists = {
    upbit: [coin("BTC")],
    bithumb: [coin("AAA", { english_name: "my foo token" }), coin("BBB", { english_name: "foo bar" })],
    binance: [coin("FOO", { symbol: "FOOUSDT" }), coin("AAA", { symbol: "AAAUSDT", english_name: "my foo token" })],
  };
  const found = searchCoins("foo", { exchange: "upbit", lists });
  // 영문명 앞부분 일치(foo bar)가 중간 일치(my foo token)보다 앞.
  assert.deepEqual(found.other.map((hit) => [hit.exchange, hit.coin.base]), [["binance", "FOO"], ["bithumb", "BBB"]]);
});

test("refreshing the first page prepends only new posts and replaces the list after a gap", () => {
  const post = (id) => ({ id });
  const held = [post("c"), post("d"), post("e")];
  // 겹치는 글(c)이 있으면 그 앞의 새 글만 위에 붙이고, 커서는 그만큼 밀린다.
  assert.deepEqual(refreshFirstPage(held, [post("a"), post("b"), post("c"), post("d")]),
    { items: ["a", "b", "c", "d", "e"].map(post), added: 2, replaced: false });
  // 한 쪽보다 많이 쌓여 하나도 안 겹치면 빈 구간을 두고 이어 붙이지 않고 첫 쪽으로 갈아 끼운다.
  assert.deepEqual(refreshFirstPage(held, [post("x"), post("y")]), { items: [post("x"), post("y")], added: 2, replaced: true });
  assert.deepEqual(refreshFirstPage([], [post("a")]), { items: [post("a")], added: 1, replaced: true });
  assert.deepEqual(refreshFirstPage(held, [post("c"), post("d")]), { items: held, added: 0, replaced: false });
});

test("the chosen trends exchange is remembered for the site-wide racer strip", async () => {
  const store = new Map();
  globalThis.localStorage = { getItem: (key) => store.get(key) ?? null, setItem: (key, value) => store.set(key, String(value)) };
  try {
    const { getTrendsExchange, setTrendsExchange } = await import("../src/lib/trendsExchange.js");
    assert.equal(getTrendsExchange(), "upbit");
    setTrendsExchange("binance");
    assert.equal(getTrendsExchange(), "binance");
    assert.equal(store.get("ggp_trends_exchange"), "binance");
    setTrendsExchange("kraken");
    assert.equal(getTrendsExchange(), "binance");
  } finally {
    delete globalThis.localStorage;
  }
});

test("the inner scroll thumb keeps a minimum size and stays inside its track", () => {
  // 칸 560 · 내용 5600 → 길 544 안에서 막대 54.4, 맨 끝이면 길 끝에 붙는다.
  const end = thumbGeometry({ clientHeight: 560, scrollHeight: 5600, scrollTop: 5040 });
  assert.equal(end.track, 544);
  assert.ok(Math.abs(end.size - 54.4) < 1e-9);
  assert.ok(Math.abs(end.offset + end.size - end.track) < 1e-9);
  assert.equal(end.atEnd, true);
  assert.equal(end.atTop, false);
  // 아주 긴 목록도 막대는 28px 아래로 줄지 않는다.
  assert.equal(thumbGeometry({ clientHeight: 226, scrollHeight: 90_000, scrollTop: 0 }).size, 28);
  // 넘치지 않으면 막대를 쓰지 않는다.
  const fits = thumbGeometry({ clientHeight: 560, scrollHeight: 400, scrollTop: 0 });
  assert.equal(fits.scrollable, false);
  assert.equal(fits.atTop && fits.atEnd, true);
});
