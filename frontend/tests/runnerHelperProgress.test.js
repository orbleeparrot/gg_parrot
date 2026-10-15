import assert from "node:assert/strict";
import test from "node:test";

import { checkRowNote, EXISTING_KEY_STEPS, helperTrack, NEW_KEY_STEPS, parseHelperProgress, problemHeadline, stageTitle } from "../src/lib/runnerHelperProgress.js";
import { launchMinVersionFor } from "../src/lib/runnerGuide.js";
import { forgetQuickRunCard, quickRunCard, rememberQuickRunCard } from "../src/lib/quickRunCard.js";

const base = { stage: "keys", exchange: "upbit", path: "new", slots: { access: true, secret: false },
  checks: { auth: "wait", ip: "wait", order: "wait" }, problem: "", connected: false };

test("only the agreed progress fields survive — keys, addresses and sentences are dropped", () => {
  const parsed = parseHelperProgress({ ...base, api_key: "K".repeat(40), slots: { access: "A".repeat(40), secret: false },
    checks: { auth: "ok", ip: "121.134.55.20", order: "bad" }, problem: "no_authorization_ip 121.134.55.20" });
  assert.deepEqual(Object.keys(parsed).sort(), ["checks", "connected", "env", "exchange", "path", "problem", "slots", "stage"]);
  assert.deepEqual(parsed.slots, { access: false, secret: false }); // 참(true)만 '찼다' 로 읽는다
  assert.deepEqual(parsed.checks, { auth: "ok", ip: "wait", order: "bad" });
  assert.equal(parsed.problem, "");
  assert.equal(JSON.stringify(parsed).includes("121.134"), false);
});

test("unknown stages or exchanges mean nothing to mirror", () => {
  assert.equal(parseHelperProgress(null), null);
  assert.equal(parseHelperProgress({ ...base, stage: "typing" }), null);
  assert.equal(parseHelperProgress({ ...base, exchange: "kraken" }), null);
});

test("new issuance has four steps and moves with the runner helper", () => {
  const states = (progress) => helperTrack(progress).map((step) => step.state);
  assert.deepEqual(helperTrack(null).map((step) => step.label), NEW_KEY_STEPS);
  assert.deepEqual(states({ ...base, stage: "ask", path: "" }), ["todo", "todo", "todo", "todo"]);
  assert.deepEqual(states({ ...base, stage: "ip" }), ["current", "todo", "todo", "todo"]);
  assert.deepEqual(states({ ...base, stage: "perm" }), ["done", "current", "todo", "todo"]);
  assert.deepEqual(states({ ...base, stage: "keys" }), ["done", "done", "current", "todo"]);
  assert.deepEqual(states({ ...base, stage: "result_fail" }), ["done", "done", "current", "todo"]);
  assert.deepEqual(states({ ...base, stage: "result_ok", connected: true }), ["done", "done", "done", "current"]);
  assert.deepEqual(states({ ...base, stage: "ready", connected: true }), ["done", "done", "done", "current"]);
  assert.deepEqual(states({ ...base, stage: "ready", connected: false }), ["todo", "todo", "todo", "todo"]);
  assert.deepEqual(states({ ...base, stage: "started", connected: true }), ["done", "done", "done", "done"]);
});

test("existing keys use the two-step track", () => {
  const existing = { ...base, stage: "existing", path: "existing" };
  assert.deepEqual(helperTrack(existing).map((step) => step.label), EXISTING_KEY_STEPS);
  assert.deepEqual(helperTrack(existing).map((step) => step.state), ["current", "todo"]);
  assert.deepEqual(helperTrack({ ...existing, stage: "result_ok", connected: true }).map((step) => step.state), ["done", "current"]);
});

test("check rows read the same words as the runner", () => {
  assert.equal(checkRowNote("ip", "bad"), "등록된 IP와 다름");
  assert.equal(checkRowNote("auth", "bad"), "키가 맞지 않음");
  assert.equal(checkRowNote("order", "na"), "확인 불가");
  assert.equal(checkRowNote("order", "wait"), "대기");
  assert.equal(checkRowNote("auth", "ok"), "");
});

test("binance progress carries its site and follows the runner's binance steps", () => {
  const states = (progress) => helperTrack(progress).map((step) => step.state);
  const demo = parseHelperProgress({ ...base, exchange: "binance", env: "demo", stage: "perm" });
  assert.equal(demo.env, "demo");
  assert.equal(parseHelperProgress({ ...base, env: "live" }).env, ""); // 국내에는 환경이 없다
  assert.equal(parseHelperProgress({ ...base, exchange: "binance", env: "mainnet" }).env, "");
  assert.deepEqual(helperTrack(demo).map((step) => step.label), ["키 만들기", "키 복사", "매크로 시작"]);
  assert.deepEqual(states(demo), ["current", "todo", "todo"]);
  assert.deepEqual(states({ ...demo, stage: "keys" }), ["done", "current", "todo"]);
  assert.deepEqual(states({ ...demo, stage: "result_ok", connected: true }), ["done", "done", "current"]);
  const live = { ...demo, env: "live" };
  assert.deepEqual(helperTrack(live).map((step) => step.label), ["키 만들기", "키 복사", "허용 IP · 주문 권한", "매크로 시작"]);
  assert.deepEqual(states({ ...live, stage: "ip" }), ["done", "done", "current", "todo"]);
  assert.deepEqual(states({ ...live, stage: "result_fail" }), ["done", "done", "current", "todo"]);
  assert.equal(stageTitle(live, "perm"), "키 만들기");
  assert.equal(stageTitle({ ...base }, "perm"), "권한 선택 · 키 발급");
});

test("binance check rows read the runner's binance words", () => {
  const practice = { exchange: "binance", env: "testnet", checks: { auth: "ok" } };
  assert.equal(checkRowNote("ip", "na", practice), "필요 없음");
  const live = { exchange: "binance", env: "live", problem: "ip", checks: { auth: "ok", ip: "bad", order: "bad" } };
  assert.equal(checkRowNote("ip", "bad", live), "IP 제한 없음");
  assert.equal(checkRowNote("order", "bad", live), "주문 권한 꺼짐");
  assert.equal(problemHeadline(live), "허용 IP 등록 필요");
  const access = { ...live, checks: { auth: "wait", ip: "bad", order: "wait" } };
  assert.equal(checkRowNote("ip", "bad", access), "IP 또는 키 불일치");
  assert.equal(problemHeadline(access), "허용 IP 확인 필요");
  assert.equal(checkRowNote("ip", "bad", { exchange: "upbit" }), "등록된 IP와 다름");
});

test("binance quick run asks for the helper runner version only when it is published", () => {
  const binance = { exchange: "binance", domestic: false };
  assert.equal(launchMinVersionFor(binance, { general: "6", domestic: "10", binanceHelper: "19" }), "19");
  assert.equal(launchMinVersionFor(binance, { general: "6", domestic: "10", binanceHelper: "" }), "6");
  assert.equal(launchMinVersionFor({ exchange: "upbit", domestic: true }, { general: "6", domestic: "10", binanceHelper: "19" }), "10");
});

test("the quick-run card is kept only for the macro it was saved for", () => {
  const card = { macro: { symbol: "KRW-BTC" }, result: { final_return_pct: 12.4 } };
  rememberQuickRunCard(501, card);
  assert.equal(quickRunCard("501"), card);
  assert.equal(quickRunCard(502), null);
  rememberQuickRunCard(502, { macro: { symbol: "KRW-ETH" } });
  assert.equal(quickRunCard(501), null); // 가장 최근 한 장만
  rememberQuickRunCard(503, {});
  assert.equal(quickRunCard(503), null); // 매크로 없는 카드는 받지 않는다
  forgetQuickRunCard();
  assert.equal(quickRunCard(502), null);
});
