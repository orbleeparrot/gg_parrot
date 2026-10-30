// 실행기(데스크톱 앱) 배포 정보 한 곳.
//
// 실행기는 orbleeparrot/ggparrot-desktop 릴리스 — Windows 설치 파일(setup.exe)과 macOS dmg 가 버전마다 있다.
// 앱 버전은 0.<연동 레벨>.<패치>이고, 연동 레벨이 서버의 runner_version(정수)과 같다.
// 서버가 알려주는 주소는 배포 변수가 서로 다른 시점에 갱신되면 옛 버전(또는 옛 exe)을 가리킬 수 있다.
// 그 보정과 방문자 OS 에 맞는 파일 고르기가 화면마다 다르면 한쪽만 고쳐지는 일이 생겨 여기에 모은다.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { getRunnerDevice } from "./runnerDevice.js";

export const OFFICIAL_APP_VERSION = "0.22.0";
export const OFFICIAL_RUNNER_VERSION = OFFICIAL_APP_VERSION.split(".")[1];
const MIN_SUPPORTED_RUNNER_VERSION = "6";
const RELEASE_BASE = "https://github.com/orbleeparrot/ggparrot-desktop/releases/download";

export function desktopAssets(appVersion) {
  const base = `${RELEASE_BASE}/v${appVersion}`;
  return {
    windowsUrl: `${base}/ggparrot-desktop_${appVersion}_windows_x64-setup.exe`,
    macUrl: `${base}/ggparrot-desktop_${appVersion}_darwin_universal.dmg`,
  };
}
export const OFFICIAL_RUNNER_DOWNLOAD_URL = desktopAssets(OFFICIAL_APP_VERSION).windowsUrl;
export const OFFICIAL_RUNNER_MAC_DOWNLOAD_URL = desktopAssets(OFFICIAL_APP_VERSION).macUrl;
export const RUNNER_PLATFORM_LABELS = { windows: "Windows", macos: "Mac" };
export const RUNNER_REQUIREMENTS = { windows: "Windows 10 이상", macos: "macOS 11 이상" };

const appParts = (value) => {
  const m = String(value || "").match(/^0\.([0-9]{1,4})\.([0-9]{1,4})$/);
  return m ? [Number(m[1]), Number(m[2])] : null;
};
const olderThanOfficial = (parts) => {
  const official = appParts(OFFICIAL_APP_VERSION);
  return !parts || parts[0] < official[0] || (parts[0] === official[0] && parts[1] < official[1]);
};
const desktopUrl = (url) => /^https:\/\/github\.com\/orbleeparrot\/ggparrot-desktop\/releases\/download\//.test(String(url || ""));
const legacyExeUrl = (url) => /^https:\/\/github\.com\/orbleeparrot\/gg_parrot\/releases\/download\/runner-v[0-9]{1,6}\/ggparrot-runner\.exe$/.test(String(url || ""));

// 이 브라우저에서 지금 실행기(데스크톱 앱)를 준비했다고 확인했는지. 옛 exe 를 열었던 기록("20" 등)은 새 앱 준비로 치지 않는다.
export const RUNNER_OPENED_STORAGE_KEY = "ggparrot:runner-opened-version";
export const LEGACY_RUNNER_OPENED_STORAGE_KEY = "ggparrot:runner-opened";
const RUNNER_OPENED_VALUE = `desktop-${OFFICIAL_RUNNER_VERSION}`;

export function isRunnerOpened() {
  try {
    return window.localStorage.getItem(RUNNER_OPENED_STORAGE_KEY) === RUNNER_OPENED_VALUE;
  } catch {
    return false;
  }
}

export function markRunnerOpened() {
  try {
    window.localStorage.removeItem(LEGACY_RUNNER_OPENED_STORAGE_KEY);
    window.localStorage.setItem(RUNNER_OPENED_STORAGE_KEY, RUNNER_OPENED_VALUE);
  } catch {
    /* 막힌 저장소 — 다음 방문에 다시 안내할 뿐이다 */
  }
}

export function fmtSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// 서버 응답(있으면) + 공식 릴리스 폴백으로 "지금 받아야 할 실행기"를 정한다. platform 은 windows | macos.
export function resolveRunnerDownload(downloadInfo, downloadError, platform = "windows") {
  const checked = downloadInfo != null || !!downloadError;
  const reported = downloadInfo?.available ? downloadInfo : null;
  // 서버가 직접 정한(공식이 아닌) 주소는 그대로 둔다 — Windows 파일 하나뿐이고 버전은 서버 값.
  const custom = reported && reported.url && !desktopUrl(reported.url) && !legacyExeUrl(reported.url);
  const reportedParts = appParts(reported?.app_version);
  const upgraded = !custom && olderThanOfficial(reportedParts);
  const appVersion = custom ? "" : upgraded ? OFFICIAL_APP_VERSION : reported.app_version;
  const assets = custom ? null : upgraded ? desktopAssets(OFFICIAL_APP_VERSION)
    : { windowsUrl: reported.url || desktopAssets(appVersion).windowsUrl, macUrl: reported.mac_url || desktopAssets(appVersion).macUrl };
  const windowsUrl = custom ? reported.url : assets.windowsUrl;
  const macUrl = custom ? "" : assets.macUrl;
  const target = platform === "macos" && macUrl ? "macos" : "windows";
  const otherPlatform = target === "macos" ? "windows" : macUrl ? "macos" : "";
  const launchReported = downloadInfo != null && Object.prototype.hasOwnProperty.call(downloadInfo, "supports_launch");
  const level = custom ? String(reported.version || "") : upgraded ? OFFICIAL_RUNNER_VERSION : String(reportedParts[0]);
  return {
    info: downloadInfo,
    error: downloadError,
    checked,
    available: checked,
    platform: target,
    platformLabel: RUNNER_PLATFORM_LABELS[target],
    requirement: RUNNER_REQUIREMENTS[target],
    url: target === "macos" ? macUrl : windowsUrl,
    windowsUrl,
    macUrl,
    otherPlatform,
    otherPlatformLabel: otherPlatform ? RUNNER_PLATFORM_LABELS[otherPlatform] : "",
    otherUrl: otherPlatform === "macos" ? macUrl : otherPlatform === "windows" ? windowsUrl : "",
    isExternal: true,
    appVersion,
    supportsLaunch: custom ? downloadInfo?.supports_launch === true : (upgraded || !launchReported || downloadInfo.supports_launch === true),
    version: level,
    minVersion: String(
      custom
        ? downloadInfo?.min_runner_version || MIN_SUPPORTED_RUNNER_VERSION
        : Math.max(Number(MIN_SUPPORTED_RUNNER_VERSION), Number(downloadInfo?.min_runner_version) || 0),
    ),
    // 국내 거래소 매크로가 요구하는 실행기 버전(서버가 내려 준다). 모르면 빈 문자열.
    domesticMinVersion: String(downloadInfo?.domestic_min_runner_version || ""),
    // 바이낸스 연결 도우미가 있는 실행기 버전 — 공개 실행기가 그 버전일 때만 서버가 내려 준다. 비어 있으면 바이낸스는 웹 4단계.
    binanceHelperMinVersion: String(downloadInfo?.binance_helper_min_runner_version || ""),
    size: downloadInfo?.size || 0,
    state: checked ? "available" : "loading",
  };
}

// 배포 정보를 받아 위 판단까지 끝낸 상태. 방문자 OS 에 맞는 파일을 고른다. `refresh` 로 다시 확인한다.
export function useRunnerDownload() {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");
  const [platform] = useState(() => getRunnerDevice().platform);

  useEffect(() => {
    let alive = true;
    api.runnerDownloadInfo()
      .then((data) => { if (alive) setInfo(data); })
      .catch((reason) => { if (alive) setError(String(reason.message || reason)); });
    return () => { alive = false; };
  }, []);

  const refresh = useCallback(async () => {
    setInfo(null);
    setError("");
    try {
      setInfo(await api.runnerDownloadInfo());
    } catch (reason) {
      setError(String(reason.message || reason));
    }
  }, []);

  return { ...resolveRunnerDownload(info, error, platform), refresh };
}
