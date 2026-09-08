import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { whaleActivityPollDelay } from "../src/features/agents/useWhaleActivity.js";

const consumers = [
  "../src/components/ChatBox.jsx",
  "../src/pages/Leaderboard.jsx",
  "../src/components/RunnerSessions.jsx",
  "../src/components/MarketContext.jsx",
  "../src/components/HotCoinsMarquee.jsx",
  "../src/features/agents/positionNews/usePositionNewsFeature.js",
  "../src/features/agents/useWhaleActivity.js",
  "../src/hooks/usePaperSession.js",
  "../src/pages/RunnerDownload.jsx",
];

for (const relativePath of consumers) {
  test(`${relativePath} uses visibility-aware non-overlapping polling`, () => {
    const source = readFileSync(new URL(relativePath, import.meta.url), "utf8");
    assert.match(source, /useAdaptivePolling/);
    if (!relativePath.endsWith("Leaderboard.jsx")) {
      assert.doesNotMatch(source, /setInterval\s*\(/);
    }
  });
}

test("position news retries only preflight AI capacity responses promptly", () => {
  const source = readFileSync(
    new URL(
      "../src/features/agents/positionNews/usePositionNewsFeature.js",
      import.meta.url,
    ),
    "utf8",
  );
  assert.match(source, /POSITION_NEWS_BUSY_RETRY_DELAYS_MS/);
  assert.match(source, /reason\?\.status !== 429/);
  assert.match(source, /requestPositionNewsWithBusyRetry/);
});

test("whale snapshot polling follows the server refresh cadence within safe bounds", () => {
  assert.equal(whaleActivityPollDelay({ refresh_seconds: 45 }), 45000);
  assert.equal(whaleActivityPollDelay({ refresh_seconds: 1 }), 10000);
  assert.equal(whaleActivityPollDelay({ refresh_seconds: 600 }), 60000);
  for (const refresh_seconds of [undefined, null, 0, -1, NaN, Infinity, "invalid"]) {
    assert.equal(whaleActivityPollDelay({ refresh_seconds }), 30000);
  }
  assert.equal(whaleActivityPollDelay(null), 30000);
});
