export const MODEL = "semif-test:0.1.1";
export const VERDICTS = { bullish: "호재", bearish: "악재", neutral: "판단 유보" };
export function elapsedSeconds(ms) { return (Math.max(0, Number(ms) || 0) / 1000).toFixed(2); }

// Use the server's elapsed duration, anchored when the snapshot arrives, so a
// user's local clock skew cannot turn a live timer negative or hours long.
export function liveStartedAt(entry, snapshot, receivedAt) {
  return receivedAt - Math.max(0, (snapshot?.server_now_ms || 0) - (entry?.started_at || 0));
}
export function phaseLabel(snapshot, error = "") {
  if (error) return "연결 확인 중";
  if (!snapshot) return "불러오는 중";
  if (!snapshot.enabled || snapshot.detail) return "서버 연결 대기";
  if (snapshot.current?.status === "processing") return "판단 중";
  return snapshot.stats?.pending > 0 ? "순차 판단 대기" : "새 뉴스 대기";
}
