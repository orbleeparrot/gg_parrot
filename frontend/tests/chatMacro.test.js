import test from "node:test";
import assert from "node:assert/strict";
import { macroIdsInText, macroText, splitMacroText } from "../src/lib/chatMacro.js";

const card = (id) => ({ entry_id: id, symbol: "BTCUSDT", username: "노희재", locked: false, human_summary: "20일선 돌파" });

test("macroText and macroIdsInText round-trip the token", () => {
  assert.equal(macroText(12), "[macro:12]");
  assert.deepEqual(macroIdsInText("보세요 [macro:12] 와 [macro:7] 와 [macro:12]"), [12, 7]);
  assert.deepEqual(macroIdsInText("토큰 없음"), []);
});

test("splitMacroText puts a card where the token is and keeps the rest as text", () => {
  const parts = splitMacroText("이거 [macro:12] 어때?", [card(12)]);
  assert.deepEqual(parts.map((p) => (p.type === "text" ? p.text : `card${p.card.entry_id}`)), ["이거", "card12", "어때?"]);
  assert.deepEqual(splitMacroText("[macro:12]", [card(12)]).map((p) => p.type), ["macro"]);
});

test("a token without a card stays plain text (deleted or unknown entry)", () => {
  assert.deepEqual(splitMacroText("없는 [macro:999] 매크로", []), [{ type: "text", text: "없는 [macro:999] 매크로" }]);
  const mixed = splitMacroText("[macro:999] 와 [macro:12]", [card(12)]);
  assert.deepEqual(mixed.map((p) => p.type), ["text", "macro"]);
  assert.equal(mixed[0].text, "[macro:999] 와");
});
