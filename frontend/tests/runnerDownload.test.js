import assert from "node:assert/strict";
import test from "node:test";
import { resolveRunnerDownload, OFFICIAL_RUNNER_DOWNLOAD_URL } from "../src/lib/runnerDownload.js";

for (let version = 1; version < 11; version += 1) {
  test(`stale v${version} deployment upgrades to published v11`, () => {
    const resolved = resolveRunnerDownload({ available: true, version: String(version), min_runner_version: String(version),
      url: OFFICIAL_RUNNER_DOWNLOAD_URL.replace("runner-v11", `runner-v${version}`), supports_launch: false }, "");
    assert.equal(resolved.url, OFFICIAL_RUNNER_DOWNLOAD_URL);
    assert.equal(resolved.version, "11");
    assert.equal(resolved.minVersion, String(Math.max(6, version)));
    assert.equal(resolved.supportsLaunch, true);
  });
}

test("a later configured release remains usable", () => {
  const url = OFFICIAL_RUNNER_DOWNLOAD_URL.replace("runner-v11", "runner-v12");
  const resolved = resolveRunnerDownload({ available: true, version: "12", min_runner_version: "12",
    url, supports_launch: true }, "");
  assert.equal(resolved.url, url);
  assert.equal(resolved.version, "12");
});

test("v11 official URL wins over stale display metadata", () => {
  const resolved = resolveRunnerDownload({ available: true, url: OFFICIAL_RUNNER_DOWNLOAD_URL, version: "6", min_runner_version: "6" }, "");
  assert.equal(resolved.version, "11");
  assert.equal(resolved.minVersion, "6");
  assert.equal(resolved.supportsLaunch, true);
});

test("API failure falls back to published v11", () => {
  const resolved = resolveRunnerDownload(null, "unavailable");
  assert.equal(resolved.url, OFFICIAL_RUNNER_DOWNLOAD_URL);
  assert.equal(resolved.version, "11");
  assert.equal(resolved.supportsLaunch, true);
});

test("custom host is not mistaken for an official stale release", () => {
  const url = "https://downloads.example.com/runner-v6/ggparrot-runner.exe";
  const resolved = resolveRunnerDownload({ available: true, url, version: "custom", supports_launch: false }, "");
  assert.equal(resolved.url, url);
  assert.equal(resolved.version, "custom");
  assert.equal(resolved.supportsLaunch, false);
});
