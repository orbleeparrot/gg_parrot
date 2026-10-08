// 실행기(exe) 배포 정보 한 곳.
//
// 서버가 알려주는 다운로드 주소는 배포 변수가 서로 다른 시점에 갱신되면 옛 버전을
// 가리킬 수 있다. 그 보정(구버전이면 현재 공식 릴리스로 올리기, 자동 연결 지원 여부,
// 표시할 버전)이 화면마다 다르게 구현되면 한쪽만 고쳐지는 일이 생긴다 — 실행
// 마법사와 설치 안내 화면이 같은 판단을 쓰도록 여기에 모아 둔다.
import { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";

export const OFFICIAL_RUNNER_VERSION = "12";
const MIN_SUPPORTED_RUNNER_VERSION = "6";
export const OFFICIAL_RUNNER_DOWNLOAD_URL =
  "https://github.com/orbleeparrot/gg_parrot/releases/download/runner-v12/ggparrot-runner.exe";

const officialVersion = (url) => String(url || "").match(
  /^https:\/\/github\.com\/orbleeparrot\/gg_parrot\/releases\/download\/runner-v([0-9]{1,6})\/ggparrot-runner\.exe$/,
)?.[1] || "";

// 이 PC 에서 실행기를 한 번 열었는지. 옛 불리언 키는 버전을 구분하지 못해서
// 현재 릴리스를 실제로 연 것과 구버전 등록이 남은 것을 섞어 버렸다.
export const RUNNER_OPENED_STORAGE_KEY = "ggparrot:runner-opened-version";
export const LEGACY_RUNNER_OPENED_STORAGE_KEY = "ggparrot:runner-opened";

export function isRunnerOpened() {
  try {
    return window.localStorage.getItem(RUNNER_OPENED_STORAGE_KEY) === OFFICIAL_RUNNER_VERSION;
  } catch {
    return false;
  }
}

export function markRunnerOpened() {
  try {
    window.localStorage.removeItem(LEGACY_RUNNER_OPENED_STORAGE_KEY);
    window.localStorage.setItem(RUNNER_OPENED_STORAGE_KEY, OFFICIAL_RUNNER_VERSION);
  } catch {
    /* 막힌 저장소 — 다음 방문에 다시 안내할 뿐이다 */
  }
}

export function fmtSize(bytes) {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

// 서버 응답(있으면) + 공식 릴리스 폴백으로 "지금 받아야 할 실행기"를 정한다.
export function resolveRunnerDownload(downloadInfo, downloadError) {
  const checked = downloadInfo != null || !!downloadError;
  const officialFallback = checked && !downloadInfo?.available && !!OFFICIAL_RUNNER_DOWNLOAD_URL;
  const available = !!downloadInfo?.available || officialFallback;
  const reportedUrl = downloadInfo?.available
    ? downloadInfo.url || api.runnerDownloadUrl
    : OFFICIAL_RUNNER_DOWNLOAD_URL;
  const reportedVersion = officialVersion(reportedUrl);
  const reportedIsOutdated = !!reportedVersion && Number(reportedVersion) < Number(OFFICIAL_RUNNER_VERSION);
  const url = reportedIsOutdated ? OFFICIAL_RUNNER_DOWNLOAD_URL : reportedUrl;
  const releaseVersion = officialVersion(url);
  const launchReported = downloadInfo != null
    && Object.prototype.hasOwnProperty.call(downloadInfo, "supports_launch");
  return {
    info: downloadInfo,
    error: downloadError,
    checked,
    available,
    url,
    isExternal: /^https?:\/\//i.test(url),
    supportsLaunch: reportedIsOutdated || downloadInfo?.supports_launch === true
      || (!launchReported && !!releaseVersion),
    version: String(
      reportedIsOutdated
        ? OFFICIAL_RUNNER_VERSION
        : releaseVersion || downloadInfo?.version || "",
    ),
    minVersion: String(
      releaseVersion
        ? Math.max(Number(MIN_SUPPORTED_RUNNER_VERSION), Number(downloadInfo?.min_runner_version) || 0)
        : downloadInfo?.min_runner_version || MIN_SUPPORTED_RUNNER_VERSION,
    ),
    // 국내 거래소 매크로가 요구하는 실행기 버전(서버가 내려 준다). 모르면 빈 문자열.
    domesticMinVersion: String(downloadInfo?.domestic_min_runner_version || ""),
    size: downloadInfo?.size || 0,
    state: !checked
      ? "loading"
      : available
        ? "available"
        : downloadError
          ? "error"
          : "unavailable",
  };
}

// 배포 정보를 받아 위 판단까지 끝낸 상태. `refresh` 로 다시 확인한다.
export function useRunnerDownload() {
  const [info, setInfo] = useState(null);
  const [error, setError] = useState("");

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

  return { ...resolveRunnerDownload(info, error), refresh };
}
