import assert from "node:assert/strict";
import test from "node:test";
import { resolveRunnerDownload, OFFICIAL_RUNNER_DOWNLOAD_URL } from "../src/lib/runnerDownload.js";

test("stale v5 deployment configuration upgrades to the published v6 runner", () => {
  const resolved = resolveRunnerDownload({ available: true, version: "5", min_runner_version: "5",
    url: OFFICIAL_RUNNER_DOWNLOAD_URL.replace("runner-v6", "runner-v5"), supports_launch: false }, "");
  assert.equal(resolved.url, OFFICIAL_RUNNER_DOWNLOAD_URL);
  assert.equal(resolved.version, "6");
  assert.equal(resolved.minVersion, "6");
  assert.equal(resolved.supportsLaunch, true);
});

test("a later configured release remains usable", () => {
  const url = OFFICIAL_RUNNER_DOWNLOAD_URL.replace("runner-v6", "runner-v7");
  const resolved = resolveRunnerDownload({ available: true, version: "7", min_runner_version: "7",
    url, supports_launch: true }, "");
  assert.equal(resolved.url, url);
  assert.equal(resolved.version, "7");
});
