import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../../api.js";

const POLL_MS = 5 * 60 * 1000;
const EMPTY_STATE = { status: "idle", sessionId: null, data: null, error: "" };

export function usePositionNewsFeature(sessionId) {
  const [state, setState] = useState(EMPTY_STATE);
  const timerRef = useRef(null);
  const requestRef = useRef(0);

  const load = useCallback(async (targetSessionId) => {
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
      const data = await api.agentPositionNews(targetSessionId);
      if (requestRef.current === requestId) {
        setState({ status: "ready", sessionId: targetSessionId, data, error: "" });
      }
    } catch (reason) {
      if (requestRef.current === requestId) {
        setState((current) => ({
          status: "error",
          sessionId: targetSessionId,
          data: String(current.sessionId) === String(targetSessionId) ? current.data : null,
          error: String(reason.message || reason),
        }));
      }
    }
  }, []);

  useEffect(() => {
    if (!sessionId) {
      setState(EMPTY_STATE);
      return undefined;
    }

    setState({ status: "idle", sessionId, data: null, error: "" });
    load(sessionId);
    const poll = () => {
      if (!document.hidden) load(sessionId);
      timerRef.current = window.setTimeout(poll, POLL_MS);
    };
    const refreshWhenVisible = () => {
      if (document.hidden) return;
      window.clearTimeout(timerRef.current);
      poll();
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    timerRef.current = window.setTimeout(poll, POLL_MS);
    return () => {
      requestRef.current += 1;
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.clearTimeout(timerRef.current);
    };
  }, [load, sessionId]);

  return useMemo(() => (
    String(state.sessionId) === String(sessionId)
      ? state
      : { ...EMPTY_STATE, sessionId: sessionId || null }
  ), [sessionId, state]);
}
