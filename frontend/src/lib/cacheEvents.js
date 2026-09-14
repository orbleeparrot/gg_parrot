const listeners = new Set();
let revision = 0;
export const leaderboardCacheVersion = () => revision;
export function subscribeLeaderboardCache(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}
export function invalidateLeaderboardCache() {
  revision += 1;
  listeners.forEach((listener) => listener());
}
