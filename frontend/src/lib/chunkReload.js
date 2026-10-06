// 배포 직후 열려 있던 탭에서 아직 안 연 화면으로 가면, 옛 해시 파일이 사라져 화면 코드를 못 받는다.
// React lazy 는 한 번 실패하면 같은 오류를 계속 던지므로 '다시 시도'로는 안 풀린다 — 새로고침만 풀린다.
const KEY = "ggp:chunk-reload-at";
const PATTERN = /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|ChunkLoadError|Loading (CSS )?chunk|Unable to preload CSS/i;

export function isChunkLoadError(error) {
  return Boolean(error) && PATTERN.test(String(error?.message || error));
}

// 한 번만 자동으로 새로고침한다 — 1분 안에 또 실패하면 사람이 누르게 둔다(무한 새로고침 방지).
export function reloadOnceForNewBuild(now = Date.now()) {
  try {
    const last = Number(sessionStorage.getItem(KEY) || 0);
    if (now - last < 60_000) return false;
    sessionStorage.setItem(KEY, String(now));
  } catch (_) {
    return false;
  }
  window.location.reload();
  return true;
}
