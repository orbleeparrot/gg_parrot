import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_SESSION_LOG_ITEMS,
  SESSION_LOG_CATEGORIES,
  formatSessionLogTime,
  matchesSessionLogCategory,
  mergeSessionLogItems,
  safeSessionLogUrl,
  sessionLogCategory,
  sessionLogKindLabel,
  sessionLogSourceLabel,
  sessionLogTimestamp,
  validateSessionLogResponse,
} from "../src/features/agents/sessionLogs.js";

const row = (id, changes = {}) => ({
  id, kind: "info", category: "execution", source: "runner",
  ts: "2026-10-12T01:02:03Z", received_at: "2026-10-12T01:02:04Z",
  message: `기록 ${id}`, ...changes,
});

test("session log categories keep all, trading, execution, news and errors distinct", () => {
  assert.deepEqual(SESSION_LOG_CATEGORIES.map(({ key }) => key), ["all", "trade", "execution", "news", "error"]);
  assert.equal(sessionLogCategory(row(1, { category: "news" })), "news");
  assert.equal(sessionLogCategory(row(1, { category: undefined, kind: "fill" })), "trade");
  assert.equal(sessionLogCategory(row(1, { category: undefined, kind: "error" })), "error");
  assert.equal(sessionLogCategory(row(1, { category: "unknown", source: "news" })), "news");
  assert.equal(sessionLogCategory(null), "execution");
});

test("log response accepts only the requested macro session and a valid items array", () => {
  const response = { session_id: 41, items: [row(1)], next_before_id: null, has_more: false };
  assert.equal(validateSessionLogResponse(response, "41"), response);
  for (const invalid of [null, {}, { session_id: 42, items: [] }, { session_id: 41, items: {} }]) {
    assert.throws(() => validateSessionLogResponse(invalid, 41), /활동 로그/);
  }
  assert.throws(() => validateSessionLogResponse({ session_id: 41, items: [] }, null), /활동 로그/);
});

test("loaded and fresh pages merge once per server id, with incoming improvements winning", () => {
  const current = [row(4), row(3), row(2)];
  const incoming = [row(5), row("3", { message: "보강된 기록" }), row(1)];
  const merged = mergeSessionLogItems(current, incoming);
  assert.deepEqual(merged.map(({ id }) => Number(id)), [5, 4, 3, 2, 1]);
  assert.equal(merged.find(({ id }) => Number(id) === 3).message, "보강된 기록");
  assert.equal(merged.length, 5);
  assert.equal(current[1].message, "기록 3");
});

test("server reception ids, not delayed occurrence timestamps, define newest and older page order", () => {
  const merged = mergeSessionLogItems([row(3)], [
    row(4, { ts: "2025-01-01T00:00:00Z" }),
    row(2, { ts: "2027-01-01T00:00:00Z" }),
    row(1, { ts: "invalid", received_at: "invalid" }),
  ]);
  assert.deepEqual(merged.map(({ id }) => id), [4, 3, 2, 1]);
});

test("merge rejects malformed identities instead of creating repeated placeholder records", () => {
  const malformed = [null, {}, row(-1), row(0), row(1.5), row(NaN), row("not-an-id"), row({}), row(Number.MAX_SAFE_INTEGER + 1)];
  assert.deepEqual(mergeSessionLogItems(malformed, [row(2), row("02")]).map(({ id }) => Number(id)), [2]);
  assert.deepEqual(mergeSessionLogItems(null, undefined), []);
});

test("all loaded records are bounded to 500 and remain latest-first", () => {
  assert.equal(MAX_SESSION_LOG_ITEMS, 500);
  const rows = Array.from({ length: 620 }, (_, index) => row(index + 1));
  const merged = mergeSessionLogItems([], rows);
  assert.equal(merged.length, 500);
  assert.equal(merged[0].id, 620);
  assert.equal(merged.at(-1).id, 121);
  assert.equal(mergeSessionLogItems([], rows, { maxItems: 10000 }).length, 500);
  assert.deepEqual(mergeSessionLogItems([], rows, { maxItems: 3 }).map(({ id }) => id), [620, 619, 618]);
});

test("event timestamps fall back to server reception, never the current browser time", () => {
  assert.equal(sessionLogTimestamp(row(1)), Date.parse("2026-10-12T01:02:03Z"));
  assert.equal(sessionLogTimestamp(row(1, { ts: "invalid" })), Date.parse("2026-10-12T01:02:04Z"));
  assert.equal(sessionLogTimestamp(row(1, { ts: "", received_at: "invalid" })), null);
  assert.equal(sessionLogTimestamp(null), null);
});

test("KST absolute timestamps include seconds and are independent of browser timezone", () => {
  assert.equal(formatSessionLogTime("2026-10-11T23:59:59Z"), "2026.10.12 08:59:59 KST");
  assert.equal(formatSessionLogTime("2026-10-12T09:00:01+09:00"), "2026.10.12 09:00:01 KST");
  assert.equal(formatSessionLogTime("2026-10-12T00:00:01"), "2026.10.12 09:00:01 KST");
  assert.equal(formatSessionLogTime(Date.parse("2026-10-12T00:00:01Z")), "2026.10.12 09:00:01 KST");
  for (const value of [undefined, null, "", "not-a-date", "0", "10/12/2026", 0, NaN, Infinity, 8.64e15 + 1]) {
    assert.equal(formatSessionLogTime(value), "시각 확인 불가");
  }
});

test("article links allow only explicit HTTP URLs without embedded credentials or controls", () => {
  assert.equal(safeSessionLogUrl("https://www.coindesk.com/markets/example?a=1&b=2"), "https://www.coindesk.com/markets/example?a=1&b=2");
  assert.equal(safeSessionLogUrl(" http://example.com/news "), "http://example.com/news");
  for (const value of [null, {}, "/relative", "//example.com", "javascript:alert(1)", "data:text/html,test", "file:///tmp/private", "https://user:password@example.com", "https://exa\nmple.com", "https://example.com/\u0000", "not-a-url"]) {
    assert.equal(safeSessionLogUrl(value), "");
  }
});

test("visible event labels distinguish order from confirmed fill without inferring trades from text", () => {
  assert.equal(sessionLogKindLabel(row(1, { kind: "order" })), "주문");
  assert.equal(sessionLogKindLabel(row(1, { kind: "fill" })), "체결");
  assert.equal(sessionLogKindLabel(row(1, { kind: "signal" })), "조건 판정");
  assert.equal(sessionLogKindLabel(row(1, { kind: "news_delivered", source: "news", category: "news" })), "뉴스 제공");
  assert.equal(sessionLogKindLabel(row(1, { kind: "unknown", message: "BUY 체결" })), "실행 기록");
});

test("source labels use bounded plain text and never synthesize unknown sources", () => {
  assert.equal(sessionLogSourceLabel(row(1)), "Windows 실행기");
  assert.equal(sessionLogSourceLabel(row(1, { source: "news" })), "뉴스 서비스");
  assert.equal(sessionLogSourceLabel(row(1, { source_label: "공용 뉴스 응답" })), "공용 뉴스 응답");
  assert.equal(sessionLogSourceLabel(row(1, { source: "unknown" })), "서버 기록");
  assert.equal(sessionLogSourceLabel(row(1, { source_label: {} })), "Windows 실행기");
});

test("news collection states never claim an article was successfully provided", () => {
  const news = (kind, status, article_count = 0) => row(1, {
    kind, source: "news", category: "news", details: { status, article_count },
  });
  assert.equal(sessionLogKindLabel(news("news", "ready", 3)), "뉴스 제공");
  for (const status of ["pending", "collecting", "empty", "idle", "unavailable", "unknown", "ready"]) {
    assert.equal(sessionLogKindLabel(news("news", status)), "뉴스 수집 상태", status);
  }
  for (const status of ["failed", "error", "rate_limited"]) {
    assert.equal(sessionLogKindLabel(news("news", status)), "뉴스 수집 오류", status);
  }
  assert.equal(sessionLogKindLabel(news("news_error", "pending")), "뉴스 수집 오류");
});

test("news failures remain visible in both news and error filters despite their news category", () => {
  const errors = [
    row(1, { kind: "news_error", source: "news", category: "news", details: { status: "failed" } }),
    row(2, { kind: "news", source: "news", category: "news", details: { status: "failed" } }),
    row(3, { kind: "news", source: "news", category: "news", details: { status: "error" } }),
  ];
  for (const item of errors) {
    assert.equal(sessionLogCategory(item), "news");
    assert.equal(matchesSessionLogCategory(item, "all"), true);
    assert.equal(matchesSessionLogCategory(item, "news"), true);
    assert.equal(matchesSessionLogCategory(item, "error"), true);
    assert.equal(matchesSessionLogCategory(item, "trade"), false);
    assert.equal(matchesSessionLogCategory(item, "execution"), false);
  }
  assert.equal(matchesSessionLogCategory(row(4, { category: "news", kind: "news", details: { status: "pending" } }), "error"), false);
  assert.equal(matchesSessionLogCategory(row(5, { category: "error", kind: "error" }), "error"), true);
});

test("production news receipt fields distinguish collection and translation failures", () => {
  const news = (changes = {}) => row(1, {
    kind: "news", category: "news", source: "news_receipt",
    details: { collection_status: "ready", translation_status: "ready", count: 3 }, ...changes,
  });
  assert.equal(sessionLogKindLabel(news()), "뉴스 제공");
  assert.equal(sessionLogKindLabel(news({ details: { collection_status: "pending", count: 0 } })), "뉴스 수집 상태");
  assert.equal(sessionLogKindLabel(news({ details: { collection_status: "empty", count: 0 } })), "뉴스 수집 상태");
  const failed = news({ kind: "news_error", details: { collection_status: "error", count: 0 } });
  assert.equal(sessionLogKindLabel(failed), "뉴스 수집 오류");
  assert.equal(matchesSessionLogCategory(failed, "error"), true);
  const translationFailure = news({ details: { collection_status: "ready", translation_status: "error", count: 3 } });
  assert.equal(sessionLogKindLabel(translationFailure), "뉴스 번역·요약 오류");
  assert.equal(matchesSessionLogCategory(translationFailure, "error"), true);
  assert.equal(matchesSessionLogCategory(translationFailure, "news"), true);
  assert.equal(sessionLogSourceLabel(news()), "뉴스 응답 수신 확인");
});
