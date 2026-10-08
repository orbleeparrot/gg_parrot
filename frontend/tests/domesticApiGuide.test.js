// 사용 설명 — 업비트·빗썸 실거래 API 키 발급 (2026-10-08).
//
// 국내 매크로 파일 실행을 연 뒤로 "그럼 키는 어디서 어떻게 만드나" 가 남아 있었다.
// 이 시험은 실제로 그려진 글을 본다 — 소스에 글자가 있느냐가 아니라.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { renderComponent, textOf } from "./renderHelper.js";
import * as runnerGuide from "../src/lib/runnerGuide.js";
const { DOMESTIC_KEY_PAGES } = runnerGuide;

const guideSource = readFileSync(new URL("../src/pages/Guide.jsx", import.meta.url), "utf8");

// embedded + initialSection 으로 그 섹션의 본문을 그린다(주소창 없이).
const section = (id) => renderComponent("src/pages/Guide.jsx",
  { embedded: true, initialSection: id }, { router: true });

test("목차에 국내 키 발급 항목이 있다", async () => {
  const html = await section("start");
  assert.match(textOf(html), /업비트 · 빗썸 실거래 API 키 발급받기/,
    "목차에 안 보이면 찾을 수 없는 문서다");
});

test("켜야 할 권한 셋과 끄라는 것 하나를 말한다", async () => {
  const text = textOf(await section("domestic-api"));
  for (const permission of ["자산조회", "주문조회", "주문하기"]) {
    assert.match(text, new RegExp(permission), `${permission} 권한 설명이 없다`);
  }
  assert.match(text, /출금하기/, "출금 권한을 다루지 않으면 사고 크기를 못 줄인다");
  assert.match(text, /절대 켜지 마세요/, "출금은 분명하게 막아야 한다");
  assert.match(text, /출금 기능이 아예 없어요/, "실행기가 출금을 못 한다는 사실(runner 에 출금 코드 없음)");
});

test("두 거래소의 발급 경로와 서로 다른 제약을 적는다", async () => {
  const text = textOf(await section("domestic-api"));
  // 업비트: PC 웹만 · Open API 관리 · 허용 IP 10개
  assert.match(text, /마이페이지 → Open API 관리/);
  assert.match(text, /모바일 앱에서는 만들 수 없어요/, "업비트는 PC 웹에서만 발급된다");
  assert.match(text, /10개까지/, "업비트 허용 IP 한도");
  // 빗썸: API 관리 · IP 5개 · 유효기간 1년 · 권한 변경 불가
  assert.match(text, /메뉴 → 계정관리 → API 관리/);
  assert.match(text, /최대 5개/, "빗썸 허용 IP 한도");
  assert.match(text, /1년이고 연장이 안 돼요/, "빗썸 키는 1년 뒤 재발급해야 한다");
  assert.match(text, /권한을 나중에 바꿀 수 없어요/, "빗썸은 활성 항목 변경이 안 된다");
  // 둘 다: Secret Key 는 한 번만 보인다
  assert.match(text, /Secret Key/);
  assert.match(text, /이 화면에서만|최초 1회만/);
});

test("국내만의 제약 — 테스트넷 없음 · 원화 현물 매수만 · 실행기 v10", async () => {
  const text = textOf(await section("domestic-api"));
  assert.match(text, /테스트넷\(가짜 자금\)이 없어요/, "바이낸스와 다른 점이고, 모르면 연습을 못 한다");
  assert.match(text, /모의 모드/, "그럼 어디서 연습하는지 말해야 한다");
  // schema.py: "국내 현물 매크로는 매수(long)·1배만 지원합니다"
  assert.match(text, /원화 현물 매수\(1배\)만/, "숏·레버리지를 기대하고 키를 만들면 헛걸음이다");
  assert.match(text, /실행기 v10 이상/, "v9 이하는 세션 시작에서 426 으로 거절된다");
  assert.match(text, /\.ggm\.json/, "무엇을 실행기에 넣는지");
});

test("허용 IP 가 바뀐다는 것을 말한다", async () => {
  const text = textOf(await section("domestic-api"));
  assert.match(text, /공인 IP/);
  assert.match(text, /사설 IP/, "192.168 을 넣고 안 된다고 하는 일이 흔하다");
  assert.match(text, /바뀔 수 있어요/, "집 인터넷 IP 변경이 '갑자기 주문이 안 나감' 의 첫 용의자다");
  assert.match(text, /실행기를 돌릴 PC/, "폰이나 Render 주소가 아닌 주문을 보내는 PC의 주소여야 한다");
  assert.match(text, /휴대폰|스마트폰/);
  assert.match(text, /Render/);
  assert.doesNotMatch(text, /10개까지 미리 넣어/, "미래 유동 IP를 예측해서 등록할 수 없다");
});

test("웹 확인은 수동 확인이고 거래소 인증은 실행기에서 한다", async () => {
  const text = textOf(await section("domestic-api"));
  assert.match(text, /연결 검사/);
  assert.match(text, /실제 주문을 보내지 않/);
  assert.match(text, /빗썸[\s\S]*주문 권한[\s\S]*확인되지 않/);
  assert.match(text, /웹[\s\S]*확인[\s\S]*인증 성공/);
  assert.match(text, /새 실행기[\s\S]*버튼이 보이면/);
  assert.match(text, /기존 실행기[\s\S]*검사 전용이 아니며[\s\S]*실제 주문/);
});

test("암호화한 로컬 키 저장은 선택이고 출금 차단도 거래 손실을 막지는 못한다", async () => {
  for (const id of ["domestic-api", "binance-api"]) {
    const text = textOf(await section(id));
    assert.match(text, /이 PC에 키 기억하기/);
    assert.match(text, /Windows[\s\S]*암호화/);
    assert.match(text, /기본[\s\S]*꺼져/);
    assert.match(text, /거래소 키[\s\S]*웹[\s\S]*서버[\s\S]*보내지 않/);
    assert.match(text, /출금[\s\S]*꺼[\s\S]*매매[\s\S]*손실/);
    assert.doesNotMatch(text, /유출돼도 자산을 빼갈 수 없/);
  }
});

test("FAQ는 거래소별 단계별 연결 도우미로 바로 연결한다", async () => {
  const html = await section("domestic-api");
  for (const exchange of ["upbit", "bithumb"]) {
    assert.ok(html.includes(`href="/exchange-connect?exchange=${exchange}"`));
  }
  assert.match(textOf(html), /단계별 연결 도우미/);
});

test("국내 연결은 실행기 IP 확인 → 공식 발급 → 실행기 검사 세 단계다", () => {
  assert.equal(typeof runnerGuide.domesticConnectionSteps, "function");
  for (const exchange of ["upbit", "bithumb"]) {
    const steps = runnerGuide.domesticConnectionSteps(exchange);
    assert.deepEqual(steps.map((step) => step.id), ["prepare", "permissions", "keys"]);
    assert.equal(steps.length, 3);
    for (const step of steps) {
      assert.ok(step.title && step.description);
      assert.ok(Array.isArray(step.checklist) && step.checklist.length > 0);
    }
    assert.match(JSON.stringify(steps[1]), /자산조회[\s\S]*주문조회[\s\S]*주문하기/);
    assert.match(JSON.stringify(steps[1]), /출금[\s\S]*(끄|꺼)/);
    assert.match(JSON.stringify(steps[0]), /실행기[\s\S]*PC[\s\S]*IPv4/);
    assert.match(JSON.stringify(steps[1]), new RegExp(`${exchange === "upbit" ? 10 : 5}개`));
    assert.match(JSON.stringify(steps[2]), /웹[\s\S]*확인[\s\S]*인증/);
    assert.match(JSON.stringify(steps[2]), /Secret Key[\s\S]*최초/);
    assert.match(JSON.stringify(steps[0]), /기존 실행기[\s\S]*직접 확인/);
    assert.match(JSON.stringify(steps[2]), /기존 실행기[\s\S]*검사 전용 버튼이 아니므로/);
    assert.equal(steps[1].action.href, DOMESTIC_KEY_PAGES[exchange].url);
  }
  assert.deepEqual(runnerGuide.domesticConnectionSteps("binance"), []);
  assert.deepEqual(runnerGuide.domesticConnectionSteps("unknown"), []);
});

test("단계 데이터는 소비자가 고쳐도 다른 세션 안내에 새지 않는다", () => {
  assert.equal(typeof runnerGuide.domesticConnectionSteps, "function");
  const steps = runnerGuide.domesticConnectionSteps("upbit");
  const originalTitle = steps[0].title;
  steps[0].title = "changed";
  steps[0].checklist.length = 0;
  steps[1].action.href = "https://untrusted.example/";
  const fresh = runnerGuide.domesticConnectionSteps("upbit");
  assert.equal(fresh[0].title, originalTitle);
  assert.ok(fresh[0].checklist.length > 0);
  assert.equal(fresh[1].action.href, DOMESTIC_KEY_PAGES.upbit.url);
});

test("기존 키 안내는 재발급 대신 권한·IP 변경과 Secret 복구를 설명한다", () => {
  const steps = runnerGuide.domesticConnectionSteps("upbit", { existingKey: true });
  assert.match(steps[1].title, /기존 키/);
  assert.match(JSON.stringify(steps), /변경[\s\S]*재발급[\s\S]*필요 없/);
  assert.match(steps[2].title, /기존 키/);
});

test("거래소 주소를 두 벌로 두지 않는다", () => {
  // 주소가 Guide 와 runnerGuide 두 곳에 글자로 있으면 한쪽만 바뀐다.
  assert.match(guideSource, /DOMESTIC_KEY_PAGES\.upbit\.url/);
  assert.match(guideSource, /DOMESTIC_KEY_PAGES\.bithumb\.url/);
  for (const page of Object.values(DOMESTIC_KEY_PAGES)) {
    assert.doesNotMatch(guideSource, new RegExp(page.url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
      `${page.domain} 주소를 Guide.jsx 에 글자로 박지 않는다`);
  }
});

test("실제로 그려진 링크가 두 거래소를 가리킨다", async () => {
  const html = await section("domestic-api");
  for (const page of Object.values(DOMESTIC_KEY_PAGES)) {
    assert.match(html, new RegExp(`href="${page.url.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}"`),
      `${page.domain} 링크가 그려지지 않았다`);
  }
  assert.match(html, /rel="noreferrer noopener"/, "새 창 링크는 referrer 를 흘리지 않는다");
});

test("검색으로 찾을 수 있다", async () => {
  // 목차 검색은 섹션의 plain `text` 를 본다. 사람들이 실제로 칠 말이 들어 있어야 한다.
  const entry = /id: "domestic-api",[\s\S]*?text: "([^"]+)"/.exec(guideSource);
  assert.ok(entry, "검색용 text 가 없다");
  for (const word of ["업비트", "빗썸", "api", "허용 ip", "시크릿", "주문하기"]) {
    assert.ok(entry[1].includes(word), `검색어 '${word}' 가 빠졌다`);
  }
});
