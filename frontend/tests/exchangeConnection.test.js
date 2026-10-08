import assert from "node:assert/strict";
import { test } from "node:test";
import jsQR from "jsqr";
import { connectionGuidePath, publicConnectionGuideUrl, parseConnectionGuide } from "../src/lib/exchangeConnection.js";
import { qrModules } from "../src/lib/connectionGuideQr.js";
import { renderComponent, textOf } from "./renderHelper.js";

test("guide continuation accepts only exchange and known step, never launch/session/key parameters", () => {
  const injected = { exchange: "upbit", step: "ip", api_key: "secret-fixture", launch_id: "ticket", member_key: "member" };
  assert.equal(connectionGuidePath(injected), "/exchange-connect?exchange=upbit&step=ip");
  assert.equal(publicConnectionGuideUrl(injected), "https://gg-parrot.vercel.app/exchange-connect?exchange=upbit&step=ip");
  assert.deepEqual(parseConnectionGuide(new URLSearchParams("exchange=bithumb&step=keys&token=fixture")), { exchange: "bithumb", step: "keys" });
  assert.deepEqual(parseConnectionGuide(new URLSearchParams("exchange=evil&step=https://evil.test")), { exchange: "upbit", step: "prepare" });
});

test("QR is generated locally with real finder patterns and four-cell quiet zone", () => {
  const modules = qrModules(publicConnectionGuideUrl({ exchange: "bithumb", step: "ip" }));
  assert.equal(modules.length, modules[0].length);
  assert.ok(modules.length > 29);
  for (let row = 0; row < 4; row += 1) assert.ok(modules[row].every((v) => v === false));
  assert.equal(modules[4][4], true);
  assert.equal(modules[5][5], false);
  assert.equal(modules[6][6], true);
});

test("independent decoder reads every exchange/step QR back to the exact public URL", () => {
  for (const exchange of ["upbit", "bithumb"]) for (const step of ["prepare", "permissions", "ip", "keys"]) {
    const address = publicConnectionGuideUrl({ exchange, step });
    const modules = qrModules(address);
    const scale = 4;
    const size = modules.length * scale;
    const pixels = new Uint8ClampedArray(size * size * 4);
    for (let y = 0; y < size; y += 1) for (let x = 0; x < size; x += 1) {
      const offset = (y * size + x) * 4;
      const value = modules[Math.floor(y / scale)][Math.floor(x / scale)] ? 0 : 255;
      pixels.set([value, value, value, 255], offset);
    }
    assert.equal(jsQR(pixels, size, size, { inversionAttempts: "dontInvert" })?.data, address);
  }
});

test("domestic guide renders one actionable step, exchange branding, and no credential input", async () => {
  const html = await renderComponent("src/components/ExchangeConnectionGuide.jsx", { exchange: "upbit", initialStep: "ip" });
  const text = textOf(html);
  assert.match(text, /실행기 PC/);
  assert.match(text, /공인 IPv4|공인 IP/);
  assert.match(text, /203\.0\.113\.10/);
  assert.match(text, /화면 예시/);
  assert.match(html, /exchanges\/upbit\.png/);
  assert.doesNotMatch(html, /<input/);
  assert.match(text, /안내만 이어보기/);
  assert.doesNotMatch(text, /인증 완료|연결 성공/);
});

test("secret step distinguishes guide from verification and warns about local opt-in save", async () => {
  const html = await renderComponent("src/components/ExchangeConnectionGuide.jsx", { exchange: "bithumb", initialStep: "keys" });
  const text = textOf(html);
  assert.match(text, /실행기/);
  assert.match(text, /암호화|Windows/);
  assert.match(text, /주문 권한|주문하기/);
  assert.match(text, /출금/);
  assert.doesNotMatch(html, /<input/);
  assert.match(html, /prefers|교환|정지 화면/);
});
