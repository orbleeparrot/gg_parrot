import assert from "node:assert/strict";
import test from "node:test";
import { chipTarget, faceSrc, kstClock, kstDay, mergeSayings, MAX_ITEMS, subText, withDayRows } from "../src/lib/agentCommentary.js";

// 2026-10-16 08:49 KST = 2026-10-15T23:49Z
const AT_0849 = Date.UTC(2026, 9, 15, 23, 49);

test("말풍선은 id 순으로 합쳐지고 같은 id 는 새 값으로 바뀐다", () => {
  const merged = mergeSayings([{ id: 2, hl: "b" }, { id: 1, hl: "a" }], [{ id: 3, hl: "c" }, { id: 2, hl: "b2" }, { id: 0 }, null]);
  assert.deepEqual(merged.map((item) => [item.id, item.hl]), [[1, "a"], [2, "b2"], [3, "c"]]);
  const many = Array.from({ length: MAX_ITEMS + 5 }, (_, index) => ({ id: index + 1 }));
  assert.equal(mergeSayings([], many).length, MAX_ITEMS);
  assert.equal(mergeSayings([], many)[0].id, 6, "넘치면 오래된 것부터 버린다");
});

test("시각과 날짜는 한국 시각으로 — UTC 로 읽어 9시간 틀리지 않게", () => {
  assert.equal(kstClock(AT_0849), "08:49");
  assert.equal(kstDay(AT_0849), "10월 16일");
  assert.equal(kstClock(0), "");
  const rows = withDayRows([
    { id: 1, at_ms: AT_0849 },
    { id: 2, at_ms: AT_0849 + 60_000 },
    { id: 3, at_ms: AT_0849 + 16 * 3_600_000 },
  ]);
  assert.deepEqual(rows.map((row) => row.type === "day" ? row.label : row.item.id), ["10월 16일", 1, 2, "10월 17일", 3]);
});

test("칩은 원문(https) · 활동 로그 줄로만 연결하고, 표정은 아는 것만 쓴다", () => {
  assert.deepEqual(chipTarget({ kind: "comm", label: "Binance Square", href: "https://www.binance.com/x" }, 7), { external: "https://www.binance.com/x" });
  assert.equal(chipTarget({ kind: "comm", label: "x", href: "javascript:alert(1)" }, 7), null);
  assert.deepEqual(chipTarget({ kind: "macro", label: "체결", event_id: 11 }, 7), { internal: "/agents?session=7&panel=logs&event=11" });
  assert.equal(chipTarget({ kind: "chart", label: "차트" }, 7), null);
  assert.equal(faceSrc("critical"), "/brand/agent/ggparrot-agent-critical-v1.svg");
  assert.equal(faceSrc("<script>"), "/brand/agent/ggparrot-agent-calm-v1.svg");
});

test("한마디는 굵은 앞부분과 나머지로 나뉜다", () => {
  assert.deepEqual(subText({ b: "손절 기준 없음", t: " · 떨어져도 매크로는 안 팔아요" }), { b: "손절 기준 없음", t: " · 떨어져도 매크로는 안 팔아요" });
  assert.deepEqual(subText("그냥 글"), { b: "", t: "그냥 글" });
  assert.deepEqual(subText({ b: 3 }), { b: "", t: "" });
});

test("채팅 필터(시안 A) — 거래 관련 · 뉴스 소식 · 손익 · 시세로 묶고, 고래 동향은 바이낸스 USDT · USDC 만", async () => {
  const { CHAT_GROUPS, inChatGroup, supportsWhale, todayCount } = await import("../src/lib/agentCommentary.js");
  assert.deepEqual(CHAT_GROUPS.map((group) => group.label), ["거래 관련", "뉴스 소식", "손익 · 시세"]);
  const say = (kind, at_ms = AT_0849) => ({ id: Math.random(), kind, at_ms });
  assert.ok(["start", "fill", "wait", "problem", "end"].every((kind) => inChatGroup(say(kind), "trade")));
  assert.ok(inChatGroup(say("post"), "news") && !inChatGroup(say("post"), "trade"));
  assert.ok(["loss", "gain", "move", "volatility", "quiet"].every((kind) => inChatGroup(say(kind), "market")));
  assert.ok(inChatGroup(say("anything"), "all") && inChatGroup(say("post"), "unknown-tab"), "모르는 탭은 전체");
  const items = [say("post"), say("post"), say("fill"), say("post", AT_0849 - 86_400_000)];
  assert.equal(todayCount(items, "news", AT_0849), 2, "탭 옆 숫자는 오늘 것만");
  assert.equal(todayCount(items, "trade", AT_0849), 1);
  assert.ok(supportsWhale("BTCUSDT") && supportsWhale("ETHUSDC"));
  assert.ok(!supportsWhale("KRW-GTC") && !supportsWhale("GTC_KRW") && !supportsWhale(""), "업비트 · 빗썸은 고래 동향 탭이 없다");
});
