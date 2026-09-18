// 회원 관리 탭의 순수 계산 — 주소 왕복(파싱 ↔ 직렬화) · API 쿼리 문자열 · 쪽 수 · 라벨 · 조치 노출 규칙 · 오류 문구.
// 화면(JSX)은 node --test 가 읽지 못하므로 판단이 들어가는 것은 전부 src/lib/memberList.js 에 있다.
import test from "node:test";
import assert from "node:assert/strict";
import { DASH } from "../src/lib/adminFormat.js";
import {
  DEFAULT_MEMBER_QUERY, MEMBER_PAGE_SIZES, MEMBER_STATUSES, MEMBER_STATUS_KEYS, SIGNUP_LABELS, blockActionKind, canActOnMember, clampPage,
  memberActionError, memberActions, memberEmail, memberQueryString, memberResultLine, memberSearchParams, memberSignup,
  memberState, memberStatusLabel, memberTier, pageCount, parseMemberQuery,
} from "../src/lib/memberList.js";

test("상태 필터: 계약의 다섯 값과 한글 라벨이 1:1", () => {
  assert.deepEqual(MEMBER_STATUS_KEYS, ["all", "active", "blocked", "deleted", "admin"]);
  assert.deepEqual(MEMBER_STATUSES.map((s) => s.label), ["전체", "활동", "차단", "탈퇴", "관리자"]);
  assert.equal(memberStatusLabel("blocked"), "차단");
  assert.equal(memberStatusLabel("deleted"), "탈퇴");
  // 주소에 이상한 값이 들어와도 화면은 "전체" 로 읽는다.
  assert.equal(memberStatusLabel("zombie"), "전체");
});

test("주소 파싱: 기본값으로 접고 60자·허용 쪽 크기만 받는다", () => {
  assert.deepEqual(parseMemberQuery(new URLSearchParams("")), DEFAULT_MEMBER_QUERY);
  assert.deepEqual(parseMemberQuery(new URLSearchParams("page=3&size=50&q=%20hui%20&status=blocked&sort=points")), {
    page: 3, pageSize: 50, q: "hui", status: "blocked", sort: "points",
  });
  // 못 쓰는 값은 전부 기본값 — 0쪽·7개·없는 필터·없는 정렬.
  assert.deepEqual(parseMemberQuery(new URLSearchParams("page=0&size=7&status=nope&sort=nope")), DEFAULT_MEMBER_QUERY);
  assert.equal(parseMemberQuery(new URLSearchParams("page=abc")).page, 1);
  assert.equal(parseMemberQuery({ q: "가".repeat(80) }).q.length, 60);
  // URLSearchParams 뿐 아니라 평범한 객체도 받는다(부모가 합쳐 준 조회 조건).
  assert.deepEqual(parseMemberQuery({ page: 2, size: 100, q: "a", status: "admin", sort: "username" }), {
    page: 2, pageSize: 100, q: "a", status: "admin", sort: "username",
  });
});

test("주소 직렬화: 기본값은 주소에서 뺀다(게시판과 같은 규칙)", () => {
  assert.equal(String(memberSearchParams(DEFAULT_MEMBER_QUERY)), "tab=members");
  assert.equal(
    String(memberSearchParams({ page: 4, pageSize: 100, q: "노희재", status: "blocked", sort: "recent" })),
    "tab=members&page=4&q=%EB%85%B8%ED%9D%AC%EC%9E%AC&status=blocked&size=100",
  );
  // 파싱 → 직렬화 → 파싱이 같은 값으로 돌아온다(새로 고침이 조회 조건을 잃지 않는다는 뜻).
  const source = new URLSearchParams("tab=members&page=2&q=hui&status=active&size=50&sort=oldest");
  const parsed = parseMemberQuery(source);
  assert.deepEqual(parseMemberQuery(memberSearchParams(parsed)), parsed);
});

test("API 쿼리 문자열: page·page_size 는 항상, 나머지는 기본값이면 생략", () => {
  assert.equal(memberQueryString({}), "page=1&page_size=25");
  assert.equal(memberQueryString({ page: 2, pageSize: 50 }), "page=2&page_size=50");
  assert.equal(
    memberQueryString({ page: 2, pageSize: 100, q: "a@b.com", status: "deleted", sort: "points" }),
    "page=2&page_size=100&q=a%40b.com&status=deleted&sort=points",
  );
  // 같은 조회 조건은 같은 문자열 — 이 문자열이 응답 캐시·폴링 키다(다른 쪽 응답이 섞이면 안 된다).
  assert.equal(memberQueryString({ page: 3, status: "all" }), memberQueryString({ page: 3 }));
  assert.notEqual(memberQueryString({ page: 3 }), memberQueryString({ page: 4 }));
});

test("쪽 수 계산과 쪽 번호 자르기", () => {
  assert.equal(pageCount(0, 25), 1);
  assert.equal(pageCount(1, 25), 1);
  assert.equal(pageCount(25, 25), 1);
  assert.equal(pageCount(26, 25), 2);
  assert.equal(pageCount(101, 25), 5);
  assert.equal(pageCount(101, 100), 2);
  assert.equal(pageCount(null, 25), 1);
  assert.equal(pageCount(100, 0), 1);
  assert.equal(clampPage(9, 3), 3);
  assert.equal(clampPage(0, 3), 1);
  assert.equal(clampPage("2", 3), 2);
  assert.equal(clampPage(undefined, undefined), 1);
  assert.ok(MEMBER_PAGE_SIZES.every((n) => pageCount(n, n) === 1));
});

test("이메일·등급·가입 방법: 빈 값은 0 도 빈칸도 아닌 대시", () => {
  assert.equal(memberEmail({ email_masked: "n***@gmail.com" }), "n***@gmail.com");
  assert.equal(memberEmail({ email_masked: "" }), DASH);
  assert.equal(memberEmail({}), DASH);
  assert.equal(memberEmail(null), DASH);
  assert.equal(memberSignup({ signup_method: "google" }), "구글");
  assert.equal(memberSignup({ signup_method: "email" }), "이메일");
  // 모르는 코드는 숨기지 않고 코드를 그대로 보여 준다(adminFormat.labelOf 와 같은 태도).
  assert.equal(memberSignup({ signup_method: "kakao" }), "kakao");
  assert.equal(memberSignup({}), DASH);
  assert.equal(memberTier({ tier_name: "골드" }), "골드");
  assert.equal(memberTier({ tier_name: "" }), DASH);
});

test("상태 알약: 탈퇴가 차단을 덮고, 관리자는 상태가 아니라 배지", () => {
  assert.deepEqual(memberState({}), { key: "active", tone: "ok", label: "활동" });
  assert.deepEqual(memberState({ is_blocked: true }), { key: "blocked", tone: "bad", label: "차단" });
  assert.deepEqual(memberState({ is_deleted: true, is_blocked: true }), { key: "deleted", tone: "off", label: "탈퇴" });
  assert.equal(memberState({ is_admin: true }).label, "활동");
});

test("조치 노출: 탈퇴·자기 계정은 전부 숨김, 관리자는 메시지만", () => {
  const plain = { id: 7, is_admin: false, is_blocked: false, is_deleted: false };
  assert.deepEqual(memberActions(plain, 1), { message: true, block: true, remove: true });
  assert.deepEqual(memberActions({ ...plain, is_admin: true }, 1), { message: true, block: false, remove: false });
  assert.deepEqual(memberActions({ ...plain, is_deleted: true }, 1), { message: false, block: false, remove: false });
  // 자기 계정은 아이디가 문자열로 와도 같은 계정으로 본다.
  assert.deepEqual(memberActions(plain, "7"), { message: false, block: false, remove: false });
  assert.equal(canActOnMember(plain, 1), true);
  assert.equal(canActOnMember({ ...plain, is_deleted: true }, 1), false);
  assert.equal(canActOnMember({ ...plain, is_admin: true }, 1), true);
  assert.equal(canActOnMember(null), false);
});

test("결과 한 줄: 차단과 해제가 갈리고, 되돌릴 수 없다는 말이 들어간다", () => {
  assert.equal(blockActionKind({ is_blocked: true }), "unblock");
  assert.equal(blockActionKind({ is_blocked: false }), "block");
  assert.equal(memberResultLine("message", { username: "hui" }), "hui 님에게 메시지를 보냈어요.");
  assert.match(memberResultLine("block", { username: "hui" }), /차단했어요/);
  assert.match(memberResultLine("block", { username: "hui" }), /채팅·게시글·댓글/);
  assert.match(memberResultLine("unblock", { username: "hui" }), /차단을 해제했어요/);
  assert.match(memberResultLine("delete", { username: "hui" }), /같은 이메일로 다시 가입할 수 없어요/);
  assert.match(memberResultLine("delete", {}), /^회원 님/);
});

test("오류 문구: 서버의 한글 detail 을 그대로, 없으면 상태코드별 기본 문구", () => {
  const server = new Error("실행 중인 매크로를 먼저 종료해 주세요.");
  server.status = 409;
  assert.equal(memberActionError(server), "실행 중인 매크로를 먼저 종료해 주세요.");
  const admin = new Error("관리자 계정은 차단·탈퇴할 수 없어요.");
  admin.status = 400;
  assert.equal(memberActionError(admin), "관리자 계정은 차단·탈퇴할 수 없어요.");
  assert.equal(memberActionError({ status: 404 }), "회원을 찾을 수 없어요.");
  assert.equal(memberActionError({ status: 400 }), "처리할 수 없는 요청이에요.");
  assert.equal(memberActionError({ status: 409 }), "실행 중인 매크로를 먼저 종료해 주세요.");
  assert.equal(memberActionError({}), "조치하지 못했어요.");
  assert.equal(memberActionError(null), "조치하지 못했어요.");
});

test("가입 방법 unknown: 탈퇴 행은 서버가 unknown 을 주므로 코드가 아니라 '알 수 없음' 으로 읽힌다", () => {
  assert.equal(SIGNUP_LABELS.unknown, "알 수 없음");
  assert.equal(memberSignup({ signup_method: "unknown" }), "알 수 없음");
  // 다른 라벨은 그대로 — unknown 추가가 기존 매핑을 건드리지 않는다.
  assert.deepEqual(Object.keys(SIGNUP_LABELS).sort(), ["email", "google", "unknown"]);
});
