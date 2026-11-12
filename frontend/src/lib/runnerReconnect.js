// 복구는 기존 세션에만 연결한다. 새 매크로 실행으로 대신하지 않는다.
export function showRunnerReconnect(session) {
  return !!session && !session.connected && !session.stopping
    && (session.status === "running" || session.recovery?.available === true);
}

export function requireReconnectTicket(data) {
  const id = Number(data?.launch_id);
  let url;
  try { url = new URL(data?.launch_url); } catch { /* 아래에서 같은 오류로 처리한다. */ }
  const params = url?.searchParams;
  if (!Number.isSafeInteger(id) || id <= 0 || !url
    || url.protocol !== "ggparrot:" || url.hostname !== "launch"
    || !["", "/"].includes(url.pathname) || url.username || url.password || url.port || url.hash
    || params.get("v") !== "2" || !["local", "production"].includes(params.get("env"))
    || !/^[A-Za-z0-9_-]{43}$/.test(params.get("ticket") || "")
    || [...params.keys()].length !== 3 || !Number.isFinite(Date.parse(data?.expires_at))) {
    throw new Error("실행기 연결 주소를 확인하지 못했어요. 다시 시도해 주세요.");
  }
  return { launch_id: id, launch_url: url.href, expires_at: data.expires_at };
}

export function reconnectTicketState(data) {
  if (data?.status === "rejected") return "outdated";
  if (["expired", "cancelled", "revoked"].includes(data?.status)) return "expired";
  if (data?.status === "claimed") return "claimed";
  return "ready";
}
