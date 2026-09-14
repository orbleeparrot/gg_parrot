import { useCallback, useEffect, useState } from "react";
import { api } from "../../api.js";
import useAdaptivePolling from "../../hooks/useAdaptivePolling.js";

// 실행 로그는 실행기가 heartbeat(5초)마다 올리니 10초 폴링이면 충분하고, 끝난 세션은
// 한 번만 읽으면 된다(로그는 더 안 늘어난다).
export function useRunnerLog(session) {
  const sessionId = session?.session_id;
  const running = !!sessionId && session.status === "running";
  const [state, setState] = useState({ sessionId: null, data: null, error: "" });

  const poll = useCallback(async (signal) => {
    try {
      const data = await api.runnerSessionEvents(sessionId, { signal });
      if (!signal.aborted) setState({ sessionId, data, error: "" });
      return { nextPollMs: 10000 };
    } catch (error) {
      if (signal.aborted) return;
      setState((previous) => ({
        sessionId,
        data: previous.sessionId === sessionId ? previous.data : null,
        error: String(error.message || error),
      }));
      throw error;
    }
  }, [sessionId]);

  useAdaptivePolling(poll, { intervalMs: 10000, maxIntervalMs: 30000, enabled: running, pollKey: sessionId });

  // 종료된 세션: 폴링 대신 한 번만 읽는다(세션이 바뀌거나 방금 끝났을 때).
  useEffect(() => {
    if (!sessionId || running) return undefined;
    const controller = new AbortController();
    api.runnerSessionEvents(sessionId, { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setState({ sessionId, data, error: "" }); })
      .catch((error) => {
        if (controller.signal.aborted) return;
        setState((previous) => ({
          sessionId,
          data: previous.sessionId === sessionId ? previous.data : null,
          error: String(error.message || error),
        }));
      });
    return () => controller.abort();
  }, [sessionId, running]);

  return state.sessionId === sessionId ? state : { data: null, error: "" };
}
