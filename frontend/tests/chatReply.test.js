import test from "node:test";
import assert from "node:assert/strict";
import { replyTargetId, replyText, stripReplyToken } from "../src/lib/chatReply.js";

test("replyText prefixes the token once and keeps the message", () => {
  assert.equal(replyText(12, " 저는 관망이요 "), "[reply:12] 저는 관망이요");
  assert.equal(replyText(12, ""), "[reply:12]");
});

test("stripReplyToken and replyTargetId read only a leading token", () => {
  assert.equal(stripReplyToken("[reply:12] 저는 관망이요"), "저는 관망이요");
  assert.equal(replyTargetId("[reply:12] 저는 관망이요"), 12);
  // 글 가운데 있는 것은 답장이 아니라 그냥 글자다.
  assert.equal(stripReplyToken("이건 [reply:12] 아님"), "이건 [reply:12] 아님");
  assert.equal(replyTargetId("이건 [reply:12] 아님"), null);
  assert.equal(replyTargetId(""), null);
});
