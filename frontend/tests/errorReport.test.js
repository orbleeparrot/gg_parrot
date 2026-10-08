import assert from "node:assert/strict";
import test from "node:test";
import { errorMessage, reportClientError, resetReportedErrors, routeTemplate, shouldReport } from "../src/lib/errorReport.js";

function fakeBrowser(pathname = "/agents") {
  const beacons = [];
  return {
    beacons,
    options: {
      location: { pathname },
      navigatorObject: { sendBeacon: (url, blob) => { beacons.push({ url, blob }); return true; } },
      fetchFn: null,
    },
  };
}

test("화면 경로는 글 번호·공유 주소를 :id 로 바꾸고 쿼리를 뗀다", () => {
  assert.equal(routeTemplate("/board/123?page=2"), "/board/:id");
  assert.equal(routeTemplate("/board/123/edit"), "/board/:id/edit");
  assert.equal(routeTemplate("/s/private-slug"), "/s/:id");
  assert.equal(routeTemplate("/mypage/settings"), "/mypage/settings");
});

test("확장 프로그램·연결 끊김·취소 같은 소음은 보내지 않는다", () => {
  assert.equal(shouldReport("ResizeObserver loop completed with undelivered notifications."), false);
  assert.equal(shouldReport("Failed to fetch"), false);
  assert.equal(shouldReport("TypeError: Cannot read properties of null (reading 'exchange')"), true);
  assert.equal(errorMessage(new TypeError("x is undefined")), "TypeError: x is undefined");
});

test("같은 오류는 한 탭에서 한 번만 — 문장·경로·빌드만 실어 보낸다", async () => {
  resetReportedErrors();
  const browser = fakeBrowser("/board/50");
  const error = new TypeError("Cannot read properties of null (reading 'exchange')");
  assert.equal(reportClientError("render", error, browser.options), true);
  assert.equal(reportClientError("render", error, browser.options), false);
  assert.equal(browser.beacons.length, 1);
  const payload = JSON.parse(await browser.beacons[0].blob.text());
  assert.deepEqual(Object.keys(payload).sort(), ["build", "kind", "message", "route"]);
  assert.equal(payload.route, "/board/:id");
  assert.equal(payload.kind, "render");
  assert.equal(browser.beacons[0].url, "/api/observability/errors");
});

test("한 탭에서 20가지까지만 보낸다", () => {
  resetReportedErrors();
  const browser = fakeBrowser();
  for (let i = 0; i < 25; i += 1) reportClientError("unhandled", new Error(`case ${i}`), browser.options);
  assert.equal(browser.beacons.length, 20);
});
