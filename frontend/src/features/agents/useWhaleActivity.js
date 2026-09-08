import { useCallback, useState } from "react";
import { api } from "../../api.js";
import useAdaptivePolling from "../../hooks/useAdaptivePolling.js";

export function whaleActivityPollDelay(data) {
  const seconds = Number(data?.refresh_seconds);
  return Number.isFinite(seconds) && seconds > 0
    ? Math.min(60, Math.max(10, seconds)) * 1000 : 30000;
}

export function useWhaleActivity(session) {
  const sessionId = session?.session_id;
  const [state, setState] = useState({ sessionId: null, data: null, error: "" });
  const poll = useCallback(async (signal) => {
    try {
      const data = await api.agentWhaleActivity(sessionId, { signal });
      if (!signal.aborted) setState({sessionId, data, error: ""});
      return { nextPollMs: whaleActivityPollDelay(data) };
    } catch (error) {
      if (signal.aborted) return;
      setState((previous) => ({sessionId,
        data: previous.sessionId === sessionId ? previous.data : null,
        error: String(error.message || error)}));
      throw error;
    }
  }, [sessionId]);
  useAdaptivePolling(poll, {intervalMs: 30000, maxIntervalMs: 60000,
    enabled: !!sessionId && session.status === "running" && session.connected,
    pollKey: sessionId});
  return state.sessionId === sessionId ? state : {data: null, error: ""};
}
