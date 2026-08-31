import { useCallback, useEffect, useRef } from "react";

import { createAdaptivePoller } from "../lib/polling.js";

export default function useAdaptivePolling(
  task,
  {
    intervalMs,
    maxIntervalMs,
    enabled = true,
    immediate = true,
    onError,
    pollKey,
  },
) {
  const taskRef = useRef(task);
  const errorRef = useRef(onError);
  const pollerRef = useRef(null);
  taskRef.current = task;
  errorRef.current = onError;

  useEffect(() => {
    if (!enabled) return undefined;
    const poller = createAdaptivePoller({
      task: ({ signal }) => taskRef.current(signal),
      intervalMs,
      maxIntervalMs,
      onError: (reason, failures) => errorRef.current?.(reason, failures),
    });
    pollerRef.current = poller;
    const syncVisibility = () => poller.setVisible(!document.hidden);
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    poller.start({ immediate });
    return () => {
      document.removeEventListener("visibilitychange", syncVisibility);
      poller.stop();
      if (pollerRef.current === poller) pollerRef.current = null;
    };
  }, [enabled, immediate, intervalMs, maxIntervalMs, pollKey]);

  return useCallback(() => pollerRef.current?.trigger(), []);
}
