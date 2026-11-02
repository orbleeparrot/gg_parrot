// 여러 종목 차트 — 위에 붙는 종목 줄로 그 차트까지 바로 내려간다 (2026-11-02).
//
// 왜 필요한가: 차트가 380px 씩 세로로 쌓여(Studio.css 의 is-multi) 다섯 종목이면
// 1,900px 를 긁어 내려야 한다.
//
// **진짜 탭이 아니다.** 차트는 그대로 다 쌓여 있고 줄은 자리만 옮겨 준다 — 한 번에
// 하나만 보이게 하면 위아래로 견주어 보던 길이 막힌다.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const render = (props) => renderComponent("src/components/ChartJumpTabs.jsx",
  { symbols: ["KRW-BTC", "KRW-ETH", "KRW-XRP"], active: "", onJump: () => {}, ...props });

test("종목마다 칸이 하나씩 나온다", async () => {
  const html = await render();
  assert.equal([...html.matchAll(/class="[^"]*\bstudio-chart-jump\b/g)].length, 3);
});

test("티커만 보여 준다 — 거래소 접두사는 뗀다", async () => {
  const text = textOf(await render());
  assert.match(text, /BTC/);
  assert.doesNotMatch(text, /KRW-/, "KRW- 가 그대로 보이면 줄이 금세 넘친다");
});

test("바이낸스 짝도 티커만 남는다", async () => {
  const text = textOf(await render({ symbols: ["BTCUSDT", "ETHUSDT"] }));
  assert.match(text, /BTC/);
  assert.doesNotMatch(text, /USDT/);
});

test("지금 보고 있는 종목에 불이 들어온다", async () => {
  const html = await render({ active: "KRW-ETH" });
  const on = [...html.matchAll(/<button[^>]*\bis-on\b[^>]*>/g)];
  assert.equal(on.length, 1, "한 번에 하나만 켜져야 한다");
  assert.match(on[0][0], /aria-current="true"/);
});

test("아무 것도 안 보고 있으면 아무 칸도 안 켜진다", async () => {
  const html = await render({ active: "" });
  assert.equal([...html.matchAll(/\bis-on\b/g)].length, 0);
});

test("종목이 하나면 줄을 안 그린다", async () => {
  // 한 종목짜리는 스크롤할 것이 없다 — 줄이 자리만 먹는다.
  assert.equal(await render({ symbols: ["KRW-BTC"] }), "");
  assert.equal(await render({ symbols: [] }), "");
  assert.equal(await render({ symbols: null }), "");
});

test("탭이 아니라 건너뛰기 줄이다", async () => {
  // role="tab" 을 달면 보조기기가 '누르면 이 화면만 보인다' 고 읽는다. 실제로는
  // 차트가 전부 그대로 있고 자리만 옮긴다.
  const html = await render();
  assert.doesNotMatch(html, /role="tab"/);
  assert.match(html, /aria-label="/);
});

test("빌더 차트 판이 이 줄을 쓴다", () => {
  // 화면 파일은 라우터·인증을 끌고 와 통째로 못 그린다 — 소스로 배선을 못 박는다.
  const src = read("../src/pages/Studio.jsx");
  assert.match(src, /import \{ ChartJumpTabs \}/);
  assert.match(src, /<ChartJumpTabs/);
});
