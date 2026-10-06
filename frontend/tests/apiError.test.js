import assert from "node:assert/strict";
import test from "node:test";
import { friendlyMessage, networkError, nonJsonMessage, NETWORK_MESSAGE } from "../src/lib/apiError.js";

test("서버가 한국어로 쓴 안내는 그대로 보여 준다", () => {
  assert.equal(friendlyMessage(409, "보관은 10건까지예요"), "보관은 10건까지예요");
});

test("영어 원문·JSON 배열은 상태 코드에 맞는 문장으로 바꾼다", () => {
  assert.match(friendlyMessage(500, "Internal Server Error"), /서버에 문제가 생겼어요/);
  assert.match(friendlyMessage(404, "macro not found"), /찾을 수 없어요/);
  assert.match(friendlyMessage(422, '[{"type":"missing"}]'), /입력한 값을 확인/);
  assert.match(friendlyMessage(418, "teapot"), /요청을 처리하지 못했어요/);
});

test("JSON 이 아닌 응답 — 500 대와 2xx HTML 을 다르게 말한다(GG-011)", () => {
  assert.match(nonJsonMessage(500), /서버에 문제가 생겼어요/);
  assert.match(nonJsonMessage(200), /예상과 다른 응답/);
});

test("연결 실패만 안내 문장으로 바꾸고 취소·시간 초과는 그대로 둔다", () => {
  const offline = networkError(new TypeError("Load failed"));
  assert.equal(offline.message, NETWORK_MESSAGE);
  assert.equal(offline.code, "NETWORK");
  const abort = new DOMException("aborted", "AbortError");
  assert.equal(networkError(abort), abort);
});
