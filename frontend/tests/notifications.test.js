import test from "node:test";
import assert from "node:assert/strict";
import {
  ACTIVITY_EVENT, appendToast, badgeText, hasNewerNotifications, isInternalLink, kindLabel, markAllReadLocal, markReadLocal,
  parseNotificationEvent, parseUnreadEvent, pointsOf,
  relativeTime, shouldSignalActivity, streamRetryDelay, unreadAfter,
} from "../src/lib/notifications.js";

test("종류 라벨 — 아는 종류는 한국어, 모르는 종류는 '알림'", () => {
  assert.equal(kindLabel("quest"), "퀘스트");
  assert.equal(kindLabel("macro_sold"), "매크로 판매");
  assert.equal(kindLabel("notice"), "공지사항");
  assert.equal(kindLabel("whatever"), "알림");
});

test("배지 글자 — 0은 빈 값, 99까지 그대로, 그 위는 99+", () => {
  assert.equal(badgeText(0), "");
  assert.equal(badgeText(undefined), "");
  assert.equal(badgeText(7), "7");
  assert.equal(badgeText(99), "99");
  assert.equal(badgeText(140), "99+");
});

test("상대 시각 — 분·시간·일, 일주일 뒤로는 KST 날짜", () => {
  const now = Date.UTC(2026, 8, 15, 6, 0, 0); // 2026-09-15 15:00 KST
  assert.equal(relativeTime(now - 20_000, now), "방금 전");
  assert.equal(relativeTime(now - 5 * 60_000, now), "5분 전");
  assert.equal(relativeTime(now - 3 * 3_600_000, now), "3시간 전");
  assert.equal(relativeTime(now - 2 * 86_400_000, now), "2일 전");
  assert.equal(relativeTime(now - 10 * 86_400_000, now), "9월 5일");
  assert.equal(relativeTime(0, now), "");
});

test("포인트 — data.points 가 양수일 때만", () => {
  assert.equal(pointsOf({ data: { points: 70 } }), 70);
  assert.equal(pointsOf({ data: { points: "10" } }), 10);
  assert.equal(pointsOf({ data: {} }), 0);
  assert.equal(pointsOf(null), 0);
});

test("읽음 처리의 로컬 반영과 남는 안 읽은 수", () => {
  const items = [{ id: 1, read: false }, { id: 2, read: true }, { id: 3, read: false }];
  const marked = markReadLocal(items, [1, 2]);
  assert.deepEqual(marked.map((it) => it.read), [true, true, false]);
  assert.equal(marked[1], items[1], "이미 읽은 행은 같은 객체를 돌려준다");
  assert.equal(unreadAfter(5, items, [1, 2]), 4, "실제로 안 읽었던 1번만 뺀다");
  assert.equal(unreadAfter(0, items, [1]), 0);
  assert.deepEqual(markAllReadLocal(items).map((it) => it.read), [true, true, true]);
});

test("앱 안 경로만 따라간다", () => {
  assert.equal(isInternalLink("/agents"), true);
  assert.equal(isInternalLink("/board/12"), true);
  assert.equal(isInternalLink("//evil.example"), false);
  assert.equal(isInternalLink("https://example.com"), false);
  assert.equal(isInternalLink(""), false);
});

test("활동 신호 — 쓰기 요청만, 알림 자체·인증 요청은 제외", () => {
  assert.equal(shouldSignalActivity("POST", "/api/backtest"), true);
  assert.equal(shouldSignalActivity("post", "/api/board/posts/3/comments?x=1"), true);
  assert.equal(shouldSignalActivity("DELETE", "/api/me/macros/4"), true);
  assert.equal(shouldSignalActivity("GET", "/api/backtest"), false);
  assert.equal(shouldSignalActivity("POST", "/api/me/notifications/read"), false);
  assert.equal(shouldSignalActivity("POST", "/api/me/notifications/stream-token"), false);
  assert.equal(shouldSignalActivity("POST", "/api/auth/login"), false);
  assert.equal(ACTIVITY_EVENT, "ggp:activity");
});

test("SSE 재연결 간격 — 1초부터 두 배, 최대 30초, ±20% 흔들림", () => {
  const fixed = () => 0.5; // 흔들림 0
  assert.equal(streamRetryDelay(0, fixed), 1000);
  assert.equal(streamRetryDelay(1, fixed), 2000);
  assert.equal(streamRetryDelay(3, fixed), 8000);
  assert.equal(streamRetryDelay(10, fixed), 30_000);
  assert.equal(streamRetryDelay(-2, fixed), 1000);
  assert.equal(streamRetryDelay(2, () => 0), 3200);
  assert.equal(streamRetryDelay(2, () => 1), 4800);
});

test("unread 이벤트 파싱 — 수와 최신 id, 수가 아니면 무시", () => {
  assert.deepEqual(parseUnreadEvent('{"unread": 3, "latest_id": 12}'), { unread: 3, latestId: 12 });
  assert.deepEqual(parseUnreadEvent('{"unread": "7"}'), { unread: 7, latestId: null });
  assert.deepEqual(parseUnreadEvent({ unread: 0, latest_id: 0 }), { unread: 0, latestId: 0 });
  assert.equal(parseUnreadEvent('{"unread": -1}'), null);
  assert.equal(parseUnreadEvent("not json"), null);
  assert.equal(parseUnreadEvent("{}"), null);
});

test("토스트 큐 — 최신이 앞, 같은 id 는 한 번, 3개까지", () => {
  let list = [];
  list = appendToast(list, { id: 1, title: "a" }, { key: 1 });
  list = appendToast(list, { id: 2, title: "b" }, { key: 2 });
  list = appendToast(list, { id: 2, title: "b again" }, { key: 3 });
  assert.deepEqual(list.map((t) => t.id), [2, 1]);
  assert.equal(list[0].item.title, "b again");
  list = appendToast(list, { id: 3 }, { key: 4 });
  list = appendToast(list, { id: 4 }, { key: 5 });
  assert.deepEqual(list.map((t) => t.id), [4, 3, 2]);
  assert.deepEqual(appendToast(list, { id: "x" }), list);
});

test("폴링 모드의 새 알림 감지 — 기준이 없으면 잡기만, 더 큰 id 가 있으면 true", () => {
  assert.equal(hasNewerNotifications(10, null), false);
  assert.equal(hasNewerNotifications(10, undefined), false);
  assert.equal(hasNewerNotifications(10, 10), false);
  assert.equal(hasNewerNotifications(11, 10), true);
  assert.equal(hasNewerNotifications("12", 10), true);
  assert.equal(hasNewerNotifications(null, 10), false);
});

test("notification 이벤트 파싱 — id 와 제목이 있어야 한 건", () => {
  assert.deepEqual(parseNotificationEvent('{"id": 5, "title": "t", "kind": "quest"}'), { id: 5, title: "t", kind: "quest" });
  assert.equal(parseNotificationEvent('{"id": "x", "title": "t"}'), null);
  assert.equal(parseNotificationEvent('{"id": 5}'), null);
  assert.equal(parseNotificationEvent("nope"), null);
});
