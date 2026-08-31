import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const consumers = [
  "../src/components/ChatBox.jsx",
  "../src/pages/Leaderboard.jsx",
  "../src/components/RunnerSessions.jsx",
  "../src/components/MarketContext.jsx",
  "../src/components/HotCoinsMarquee.jsx",
  "../src/features/agents/positionNews/usePositionNewsFeature.js",
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
