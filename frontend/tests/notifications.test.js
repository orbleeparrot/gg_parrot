import test from "node:test";
import assert from "node:assert/strict";
import {
  badgeText, isInternalLink, kindLabel, markAllReadLocal, markReadLocal, pointsOf, relativeTime, unreadAfter,
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
