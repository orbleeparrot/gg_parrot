import { useCallback, useMemo, useState } from "react";
import { api } from "../../api.js";
import useAdaptivePolling from "../../hooks/useAdaptivePolling.js";

export function whaleActivityPollDelay(data) {
  const seconds = Number(data?.refresh_seconds);
  return Number.isFinite(seconds) && seconds > 0
    ? Math.min(60, Math.max(10, seconds)) * 1000 : 30000;
}

export function supportsWhaleActivity(session) {
  const exchange = String(session?.macro?.exchange || session?.exchange || "binance").toLowerCase();
  return exchange === "binance" && !String(session?.symbol || "").toUpperCase().startsWith("KRW-");
}

export function useWhaleActivity(session) {
  const sessionId = session?.session_id;
  const supported = supportsWhaleActivity(session);
  const unsupportedState = useMemo(() => ({ sessionId, error: "", data: {
    feature_key: "whale_activity", symbol: session?.symbol || "", status: "unavailable",
    error: "unsupported_pair", items: [], stale: false,
  } }), [sessionId, session?.symbol]);
  const [state, setState] = useState({ sessionId: null, data: null, error: "" });
  const poll = useCallback(async (signal) => {
    if (!supported) return { nextPollMs: null };
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
  }, [sessionId, supported]);
  useAdaptivePolling(poll, {intervalMs: 30000, maxIntervalMs: 60000,
    enabled: !!sessionId && supported && session.status === "running" && session.connected,
    pollKey: sessionId});
  if (sessionId && !supported) return unsupportedState;
  return state.sessionId === sessionId ? state : {data: null, error: ""};
}
