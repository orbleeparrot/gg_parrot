import assert from "node:assert/strict";
import test from "node:test";
import { coinName, macroSourceBadge, readMacroSource } from "../src/lib/macroSource.js";

// 조건 판 머리의 출처 배지 (2026-09-23): 글자는 티커 + 종목명, 출처는 색(tone)으로만.
test("macroSourceBadge: ticker + korean name, tone follows the source", () => {
  const b = macroSourceBadge({ kind: "ask", label: "정기 분할매수 · 1h" }, "BTCUSDT");
  assert.equal(b.kind, "ask");
  assert.equal(b.tone, "ask");
  assert.equal(b.ticker, "BTC");
  assert.equal(b.name, "비트코인");
  assert.equal(b.more, 0);
  assert.equal(b.title, "껄무새 후보 · 정기 분할매수 · 1h · BTC 비트코인");
});

test("macroSourceBadge: no source → 직접 설정 with neutral tone; unknown coin shows ticker only", () => {
  const b = macroSourceBadge(null, "HEMIUSDT");
  assert.equal(b.kind, "none");
  assert.equal(b.tone, "none");
  assert.equal(b.ticker, "HEMI");
  assert.equal(b.name, "");
  assert.equal(b.title, "직접 설정 · HEMI");
});

test("macroSourceBadge: multi-symbol form shows the first ticker and how many more", () => {
  const b = macroSourceBadge({ kind: "board" }, "SOLUSDT, ETHUSDT, XRPUSDT");
  assert.equal(b.ticker, "SOL");
  assert.equal(b.more, 2);
  assert.equal(b.title, "리더보드에서 복사 · SOL 외 2종목");
});

test("macroSourceBadge: shared link and upload map to their tones; empty symbol is safe", () => {
  assert.equal(macroSourceBadge({ kind: "shared" }, "BTCUSDT").tone, "board");
  assert.equal(macroSourceBadge({ kind: "file", label: "내 전략" }, "").ticker, "");
  assert.equal(macroSourceBadge({ kind: "file", label: "내 전략" }, "").title, "매크로 업로드 · 내 전략");
});

test("readMacroSource: accepts known kinds only, drops the legacy loadedFrom string", () => {
  assert.equal(readMacroSource("껄무새가 고른 후보 · 정기 분할매수"), null);
  assert.equal(readMacroSource({ kind: "nope" }), null);
  assert.deepEqual(readMacroSource({ kind: "guide", label: 3 }), { kind: "guide", label: "" });
  assert.deepEqual(readMacroSource({ kind: "file", label: "내 전략" }), { kind: "file", label: "내 전략" });
});

test("coinName: base or full symbol, 1000-prefixed perps included, unknown → empty", () => {
  assert.equal(coinName("BTC"), "비트코인");
  assert.equal(coinName("solusdt"), "솔라나");
  assert.equal(coinName("1000PEPEUSDT"), "페페");
  assert.equal(coinName("ZZZZUSDT"), "");
  assert.equal(coinName(""), "");
});
