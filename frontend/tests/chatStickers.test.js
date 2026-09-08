import assert from "node:assert/strict";
import test from "node:test";

import { STICKERS, stickerFromText, stickerText } from "../src/lib/chatStickers.js";

test("a sticker message round-trips through its text token", () => {
  for (const sticker of STICKERS) {
    assert.equal(stickerFromText(stickerText(sticker.id))?.id, sticker.id);
  }
  assert.equal(STICKERS.length, 6);
});

test("only a whole-message token counts as a sticker", () => {
  assert.equal(stickerFromText("  [sticker:calm] "), STICKERS[0]);
  assert.equal(stickerFromText("hi [sticker:calm]"), null);
  assert.equal(stickerFromText("[sticker:nope]"), null);
  assert.equal(stickerFromText(""), null);
});
