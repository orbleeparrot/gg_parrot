import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import useAdaptivePolling from "../../hooks/useAdaptivePolling.js";
import { mergeSayings } from "../../lib/agentCommentary.js";

// 껄무새 중계 말풍선 — 서버가 만든 것을 받은 마지막 id 뒤로만 이어 받는다.
// 실행 중이면 5초마다, 끝난 세션은 한 번(종료 말풍선이 늦게 붙을 수 있어 잠깐 더 본다).
export function useAgentCommentary(session) {
  const sessionId = session?.session_id;
  const running = !!sessionId && session.status === "running";
  const [state, setState] = useState({ sessionId: null, items: [], cursor: 0, loaded: false, error: "" });
  const cursorRef = useRef(0);

  useEffect(() => {
    cursorRef.current = 0;
    setState({ sessionId, items: [], cursor: 0, loaded: false, error: "" });
  }, [sessionId]);

  const load = useCallback(async (signal) => {
    if (!sessionId) return;
    const data = await api.runnerSessionAgent(sessionId, cursorRef.current, { signal });
    if (signal?.aborted) return;
    const items = Array.isArray(data?.items) ? data.items : [];
    const cursor = Number.isSafeInteger(data?.cursor) ? data.cursor : cursorRef.current;
    cursorRef.current = Math.max(cursorRef.current, cursor);
    setState((previous) => {
      const base = previous.sessionId === sessionId ? previous : { sessionId, items: [], cursor: 0 };
      return { sessionId, items: mergeSayings(base.items, items), cursor: cursorRef.current, loaded: true, error: "" };
    });
  }, [sessionId]);

  const poll = useCallback(async (signal) => {
    try {
      await load(signal);
      return { nextPollMs: 5000 };
    } catch (error) {
      if (signal?.aborted) return undefined;
      setState((previous) => ({ ...previous, sessionId, loaded: true, error: String(error.message || error) }));
      throw error;
    }
  }, [load, sessionId]);

  useAdaptivePolling(poll, { intervalMs: 5000, maxIntervalMs: 30000, enabled: running, pollKey: sessionId });

  // 끝난 세션: 폴링 대신 한 번 읽고, 종료 말풍선이 붙을 시간을 한 번 더 준다.
  useEffect(() => {
    if (!sessionId || running) return undefined;
    const controller = new AbortController();
    const read = () => load(controller.signal).catch((error) => {
      if (!controller.signal.aborted) setState((previous) => ({ ...previous, sessionId, loaded: true, error: String(error.message || error) }));
    });
    read();
    const timer = window.setTimeout(read, 15000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [load, running, sessionId]);

  return state.sessionId === sessionId ? state : { sessionId, items: [], cursor: 0, loaded: false, error: "" };
}
