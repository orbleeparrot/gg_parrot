import assert from "node:assert/strict";
import test from "node:test";
import { actionableId, chipTarget, faceSrc, kstClock, kstDay, mergeSayings, MAX_ITEMS, subText, withDayRows } from "../src/lib/agentCommentary.js";

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

test("손실 말풍선 버튼은 실행 중 · 보유 중일 때 가장 최근 손실 말풍선에만", () => {
  const items = [{ id: 1, kind: "loss", actions: ["close_and_stop", "dismiss"] }, { id: 2, kind: "volatility" },
    { id: 3, kind: "loss", actions: ["close_and_stop", "dismiss"] }];
  const holding = { status: "running", in_position: true };
  assert.equal(actionableId(items, holding), 3);
  assert.equal(actionableId(items, { ...holding, in_position: false }), null);
  assert.equal(actionableId(items, { ...holding, stopping: true }), null);
  assert.equal(actionableId([...items, { id: 4, kind: "fill" }], holding), null, "그 뒤로 사고판 일이 있으면 지난 이야기");
});

test("한마디는 굵은 앞부분과 나머지로 나뉜다", () => {
  assert.deepEqual(subText({ b: "손절 기준 없음", t: " · 떨어져도 매크로는 안 팔아요" }), { b: "손절 기준 없음", t: " · 떨어져도 매크로는 안 팔아요" });
  assert.deepEqual(subText("그냥 글"), { b: "", t: "그냥 글" });
  assert.deepEqual(subText({ b: 3 }), { b: "", t: "" });
});
