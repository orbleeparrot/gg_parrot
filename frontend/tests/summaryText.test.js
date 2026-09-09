import assert from "node:assert/strict";
import test from "node:test";

import { splitSummary } from "../src/lib/summaryText.js";

test("a multi-line summary becomes a lead line plus body lines", () => {
  const parsed = splitSummary("오늘 코인 시장은 금리 기대감에 올랐다.\n비트코인이 8만1천 달러를 넘었다.\n\n한국은 규제 논의가 이어진다.\n");
  assert.equal(parsed.lead, "오늘 코인 시장은 금리 기대감에 올랐다.");
  assert.deepEqual(parsed.body, ["비트코인이 8만1천 달러를 넘었다.", "한국은 규제 논의가 이어진다."]);
});

test("a single block without line breaks splits at the first sentence", () => {
  const parsed = splitSummary("오늘 코인 시장은 올랐다. 비트코인이 8만 달러를 넘었고 이더리움도 강세다.");
  assert.equal(parsed.lead, "오늘 코인 시장은 올랐다.");
  assert.deepEqual(parsed.body, ["비트코인이 8만 달러를 넘었고 이더리움도 강세다."]);
  assert.deepEqual(splitSummary("한 문장뿐이다."), { lead: "한 문장뿐이다.", body: [] });
  assert.equal(splitSummary("   "), null);
});
