import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../../api.js";
import useAdaptivePolling from "../../../hooks/useAdaptivePolling.js";

const POLL_MS = 5 * 60 * 1000;
const EMPTY_STATE = { status: "idle", sessionId: null, data: null, error: "" };

export function usePositionNewsFeature(sessionId) {
  const [state, setState] = useState(EMPTY_STATE);
  const requestRef = useRef(0);

  const load = useCallback(async (targetSessionId, signal) => {
    if (!targetSessionId) return;
    const requestId = requestRef.current + 1;
    requestRef.current = requestId;
    setState((current) => ({
      status: "loading",
      sessionId: targetSessionId,
      data: String(current.sessionId) === String(targetSessionId) ? current.data : null,
      error: "",
    }));
    try {
      const data = await api.agentPositionNews(targetSessionId, { signal });
      if (requestRef.current === requestId) {
        setState({ status: "ready", sessionId: targetSessionId, data, error: "" });
      }
    } catch (reason) {
      if (reason?.name === "AbortError") return;
      if (requestRef.current === requestId) {
        setState((current) => ({
          status: "error",
          sessionId: targetSessionId,
          data: String(current.sessionId) === String(targetSessionId) ? current.data : null,
          error: String(reason.message || reason),
        }));
      }
      throw reason;
    }
  }, []);

  const poll = useCallback(
    (signal) => load(sessionId, signal),
    [load, sessionId],
  );
  useAdaptivePolling(poll, {
    intervalMs: POLL_MS,
    maxIntervalMs: 30 * 60 * 1000,
    enabled: !!sessionId,
    pollKey: sessionId,
  });

  useEffect(() => {
    if (!sessionId) {
      setState(EMPTY_STATE);
      return undefined;
    }

    setState({ status: "idle", sessionId, data: null, error: "" });
    return () => {
      requestRef.current += 1;
    };
  }, [sessionId]);

  return useMemo(() => (
    String(state.sessionId) === String(sessionId)
      ? state
      : { ...EMPTY_STATE, sessionId: sessionId || null }
  ), [sessionId, state]);
}
