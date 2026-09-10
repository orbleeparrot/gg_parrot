import assert from "node:assert/strict";
import test from "node:test";
import { getRunnerDevice } from "../src/lib/runnerDevice.js";

test("Windows desktop and touch PCs support the runner regardless of viewport", () => {
  for (const device of [
    { platform: "Win32", userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)" },
    { userAgentData: { platform: "Windows", mobile: false } },
    { platform: "Win32", maxTouchPoints: 10, userAgent: "Windows NT 10.0" },
  ]) {
    assert.equal(getRunnerDevice(device).canRunWindowsRunner, true);
    assert.equal(getRunnerDevice(device).isMobile, false);
  }
});

test("phone and tablet operating systems never offer a Windows launch", () => {
  for (const device of [
    { platform: "iPhone", userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X)" },
    { platform: "iPad", userAgent: "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X)" },
    // Safari on iPad requests desktop websites and identifies as a Mac.
    { platform: "MacIntel", maxTouchPoints: 5, userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15)" },
    { platform: "Linux armv8l", userAgent: "Mozilla/5.0 (Linux; Android 15; Pixel 9) Mobile" },
    { userAgentData: { platform: "Android", mobile: false } },
    { userAgent: "Mozilla/5.0 (Windows Phone 10.0; Android 6.0; Microsoft) Mobile" },
  ]) {
    assert.equal(getRunnerDevice(device).canRunWindowsRunner, false);
    assert.equal(getRunnerDevice(device).isMobile, true);
  }
});

test("Mac, Linux, ChromeOS, and unknown platforms use PC handoff", () => {
  for (const device of [
    { platform: "MacIntel", maxTouchPoints: 0 },
    { platform: "Linux x86_64" },
    { userAgent: "Mozilla/5.0 (X11; CrOS x86_64 15917.0.0)" },
    {}, null,
  ]) assert.equal(getRunnerDevice(device).canRunWindowsRunner, false);
});
