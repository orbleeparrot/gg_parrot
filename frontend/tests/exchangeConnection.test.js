import assert from "node:assert/strict";
import { test } from "node:test";
import { connectionGuidePath, parseConnectionGuide } from "../src/lib/exchangeConnection.js";
import { renderComponent, textOf } from "./renderHelper.js";

test("public guide keeps old IP links compatible and accepts no credential or launch fields", () => {
  const injected = { exchange: "upbit", step: "ip", api_key: "secret-fixture", launch_id: "ticket", member_key: "member" };
  assert.equal(connectionGuidePath(injected), "/exchange-connect?exchange=upbit&step=prepare");
  assert.deepEqual(parseConnectionGuide(new URLSearchParams("exchange=bithumb&step=ip&token=fixture")), { exchange: "bithumb", step: "prepare" });
  assert.deepEqual(parseConnectionGuide(new URLSearchParams("exchange=bithumb&step=keys&token=fixture")), { exchange: "bithumb", step: "keys" });
  assert.deepEqual(parseConnectionGuide(new URLSearchParams("exchange=evil&step=https://evil.test")), { exchange: "upbit", step: "prepare" });
});

test("runner-first guide explains actual PC IP lookup without fake video, own QR, or credential input", async () => {
  for (const exchange of ["upbit", "bithumb"]) {
    const html = await renderComponent("src/components/ExchangeConnectionGuide.jsx", { exchange, initialStep: "prepare" });
    const text = textOf(html);
    for (const expected of [/거래소 연결 도우미/, /공인 IPv4 확인/, /ipify/, /고정 IP/, /VPN|프록시/, /기존 실행기/, /1 \/ 3/]) assert.match(text, expected);
    assert.match(html, new RegExp(`exchanges/${exchange}\\.png`));
    assert.doesNotMatch(html, /<input|<video|<svg|exchange-connect-example/);
    assert.doesNotMatch(text, /203\.0\.113\.10|화면 예시|반복 재생|정지 화면|안내만 이어보기|인증 완료|연결 성공/);
  }
});

test("official QR login is not API-key transport or third-party trading approval", async () => {
  const text = textOf(await renderComponent("src/components/ExchangeConnectionGuide.jsx", { exchange: "upbit", initialStep: "permissions" }));
  assert.match(text, /업비트 공식 QR 로그인/);
  assert.match(text, /API 키[\s\S]*전송[\s\S]*아니/);
  assert.match(text, /직접[\s\S]*허용 IP|허용 IP[\s\S]*직접/);
});

test("Bithumb uses API2 help and possession authentication, not separate email activation", async () => {
  const html = await renderComponent("src/components/ExchangeConnectionGuide.jsx", { exchange: "bithumb", initialStep: "permissions" });
  assert.match(html, /52815899880345/);
  assert.match(textOf(html), /점유 인증/);
  assert.match(textOf(html), /즉시 활성/);
  assert.doesNotMatch(textOf(html), /이메일 활성|활성화 메일/);
});

test("key step keeps local opt-in storage and native verification distinct from live start", async () => {
  const html = await renderComponent("src/components/ExchangeConnectionGuide.jsx", { exchange: "bithumb", initialStep: "keys" });
  const text = textOf(html);
  assert.match(text, /실행기/);
  assert.match(text, /Windows[\s\S]*암호화/);
  assert.match(text, /기본[\s\S]*꺼져/);
  assert.match(text, /주문 권한[\s\S]*확인되지 않/);
  assert.match(text, /실제 주문을 보내지 않/);
  assert.match(text, /웹[\s\S]*인증[\s\S]*아니/);
  assert.match(text, /기존 실행기[\s\S]*검사 전용[\s\S]*실제 주문/);
  assert.match(text, /출금/);
  assert.doesNotMatch(html, /<input/);
});
