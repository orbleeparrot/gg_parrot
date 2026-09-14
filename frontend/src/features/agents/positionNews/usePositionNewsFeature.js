import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../../api.js";
import useAdaptivePolling from "../../../hooks/useAdaptivePolling.js";
import { mergePositionNews } from "./feed.js";

const POLL_MS = 3000;
const POSITION_NEWS_BUSY_RETRY_DELAYS_MS = [400, 1_200, 2_400];
const EMPTY_STATE = { status: "idle", sessionId: null, data: null, error: "" };

function waitForRetry(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      const error = new Error("Request aborted");
      error.name = "AbortError";
      reject(error);
      return;
    }
    const onAbort = () => {
      window.clearTimeout(timer);
      const error = new Error("Request aborted");
      error.name = "AbortError";
      reject(error);
    };
    const timer = window.setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

async function requestPositionNewsWithBusyRetry(sessionId, signal, cursor) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await api.agentPositionNews(sessionId, { signal, cursor });
    } catch (reason) {
      if (
        reason?.status !== 429
        || attempt >= POSITION_NEWS_BUSY_RETRY_DELAYS_MS.length
      ) {
        throw reason;
      }
      await waitForRetry(POSITION_NEWS_BUSY_RETRY_DELAYS_MS[attempt], signal);
    }
  }
}

export function usePositionNewsFeature(sessionId, running = true) {
  const [state, setState] = useState(EMPTY_STATE);
  const requestRef = useRef(0);
  const cursorRef = useRef({ sessionId: null, cursor: undefined });

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
      const cursor = String(cursorRef.current.sessionId) === String(targetSessionId)
        ? cursorRef.current.cursor : undefined;
      const data = await requestPositionNewsWithBusyRetry(targetSessionId, signal, cursor);
      if (requestRef.current === requestId) {
        cursorRef.current = { sessionId: targetSessionId, cursor: data.cursor };
        setState((current) => ({ status: "ready", sessionId: targetSessionId,
          data: mergePositionNews(String(current.sessionId) === String(targetSessionId) ? current.data : null, data),
          error: "" }));
      }
      return { nextPollMs: data.has_more ? 100 : !running ? null : POLL_MS };
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
  }, [running]);

  const poll = useCallback(
    (signal) => load(sessionId, signal),
    [load, sessionId],
  );
  useAdaptivePolling(poll, {
    intervalMs: POLL_MS,
    maxIntervalMs: 30000,
    enabled: !!sessionId,
    pollKey: `${sessionId}:${running}`,
  });

  useEffect(() => {
    if (!sessionId) {
      setState(EMPTY_STATE);
      return undefined;
    }

    setState({ status: "idle", sessionId, data: null, error: "" });
    cursorRef.current = { sessionId, cursor: undefined };
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
