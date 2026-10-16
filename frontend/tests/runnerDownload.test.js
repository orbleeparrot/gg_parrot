import assert from "node:assert/strict";
import test from "node:test";
import {
  OFFICIAL_APP_VERSION, OFFICIAL_RUNNER_DOWNLOAD_URL, OFFICIAL_RUNNER_MAC_DOWNLOAD_URL, desktopAssets, resolveRunnerDownload,
} from "../src/lib/runnerDownload.js";

const LEGACY = (n) => `https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v${n}/ggparrot-runner.exe`;

test("공식 주소는 데스크톱 앱 릴리스의 Windows 설치 파일 · macOS dmg", () => {
  assert.equal(OFFICIAL_RUNNER_DOWNLOAD_URL,
    `https://github.com/orbleeparrot/ggparrot-desktop/releases/download/v${OFFICIAL_APP_VERSION}/ggparrot-desktop_${OFFICIAL_APP_VERSION}_windows_x64-setup.exe`);
  assert.equal(OFFICIAL_RUNNER_MAC_DOWNLOAD_URL,
    `https://github.com/orbleeparrot/ggparrot-desktop/releases/download/v${OFFICIAL_APP_VERSION}/ggparrot-desktop_${OFFICIAL_APP_VERSION}_darwin_universal.dmg`);
});

test("방문자 OS 에 맞는 파일을 고르고 다른 OS 파일도 함께 준다", () => {
  const info = { available: true, app_version: OFFICIAL_APP_VERSION, version: "20", url: OFFICIAL_RUNNER_DOWNLOAD_URL,
    mac_url: OFFICIAL_RUNNER_MAC_DOWNLOAD_URL, supports_launch: true, min_runner_version: "6" };
  const win = resolveRunnerDownload(info, "", "windows");
  assert.equal(win.url, OFFICIAL_RUNNER_DOWNLOAD_URL);
  assert.equal(win.platformLabel, "Windows");
  assert.equal(win.otherUrl, OFFICIAL_RUNNER_MAC_DOWNLOAD_URL);
  assert.equal(win.otherPlatformLabel, "Mac");
  const mac = resolveRunnerDownload(info, "", "macos");
  assert.equal(mac.url, OFFICIAL_RUNNER_MAC_DOWNLOAD_URL);
  assert.equal(mac.requirement, "macOS 11 이상");
  assert.equal(mac.otherUrl, OFFICIAL_RUNNER_DOWNLOAD_URL);
  // 휴대폰 · 리눅스처럼 실행할 수 없는 곳은 Windows 파일을 기본으로 둔다(주소 복사 안내용).
  assert.equal(resolveRunnerDownload(info, "", "linux").url, OFFICIAL_RUNNER_DOWNLOAD_URL);
  assert.equal(win.version, "20");
  assert.equal(win.appVersion, OFFICIAL_APP_VERSION);
});

for (const version of [6, 19, 20]) {
  test(`옛 서버의 exe v${version} 주소는 데스크톱 앱으로 올린다`, () => {
    const resolved = resolveRunnerDownload({ available: true, version: String(version), url: LEGACY(version),
      supports_launch: false, min_runner_version: String(version) }, "", "windows");
    assert.equal(resolved.url, OFFICIAL_RUNNER_DOWNLOAD_URL);
    assert.equal(resolved.version, "20");
    assert.equal(resolved.minVersion, String(Math.max(6, version)));
    assert.equal(resolved.supportsLaunch, true);
  });
}

test("더 새 앱 버전은 따른다", () => {
  const next = desktopAssets("0.21.0");
  const resolved = resolveRunnerDownload({ available: true, app_version: "0.21.0", version: "21", url: next.windowsUrl,
    mac_url: next.macUrl, supports_launch: true, min_runner_version: "6" }, "", "macos");
  assert.equal(resolved.url, next.macUrl);
  assert.equal(resolved.version, "21");
});

test("API 가 실패하면 공식 데스크톱 앱으로", () => {
  const resolved = resolveRunnerDownload(null, "unavailable", "windows");
  assert.equal(resolved.url, OFFICIAL_RUNNER_DOWNLOAD_URL);
  assert.equal(resolved.version, "20");
  assert.equal(resolved.supportsLaunch, true);
  assert.equal(resolved.state, "available");
  assert.equal(resolveRunnerDownload(null, "", "windows").state, "loading");
});

test("서버가 정한 다른 주소는 그대로 둔다", () => {
  const url = "https://downloads.example.com/runner-v6/ggparrot-runner.exe";
  const resolved = resolveRunnerDownload({ available: true, url, version: "custom", supports_launch: false }, "", "macos");
  assert.equal(resolved.url, url);
  assert.equal(resolved.macUrl, "");
  assert.equal(resolved.otherUrl, "");
  assert.equal(resolved.version, "custom");
  assert.equal(resolved.supportsLaunch, false);
});
