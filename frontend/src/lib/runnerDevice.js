// The desktop runner is a Windows executable. Screen width alone says nothing
// about whether it can run: a narrow Windows window is valid, an iPad is not.
export function getRunnerDevice(device = globalThis.navigator) {
  const ua = String(device?.userAgent || "");
  const platform = String(device?.userAgentData?.platform || device?.platform || "");
  const ipadDesktopMode = /Mac/i.test(platform) && Number(device?.maxTouchPoints || 0) > 1;
  const ios = ipadDesktopMode || /iPad|iPhone|iPod/i.test(`${platform} ${ua}`);
  const android = /Android/i.test(`${platform} ${ua}`);
  const isMobile = ios || android || device?.userAgentData?.mobile === true
    || /Mobile|Windows Phone/i.test(ua);
  const windows = !isMobile && /Win(?:dows|32|64|CE|NT)/i.test(`${platform} ${ua}`);
  return {
    canRunWindowsRunner: windows,
    isMobile,
    platform: ios ? "ios" : android ? "android" : windows ? "windows"
      : /Mac/i.test(`${platform} ${ua}`) ? "macos"
        : /Linux|CrOS/i.test(`${platform} ${ua}`) ? "linux" : "unknown",
  };
}
