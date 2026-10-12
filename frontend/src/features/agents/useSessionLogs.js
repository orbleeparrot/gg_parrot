import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { useAccountGuard } from "../../lib/auth.js";
import useAdaptivePolling from "../../hooks/useAdaptivePolling.js";
import { mergeSessionLogItems, validateSessionLogResponse } from "./sessionLogs.js";

const empty = (key) => ({ key, items: [], status: "loading", error: "", hasMore: false,
  nextBeforeId: null, retention: null, loadingMore: false, olderLoaded: false });

// Fetch only while this menu is open. A completed run is immutable here and
// needs one read, not a permanent poll. Every update is account/session scoped.
export function useSessionLogs(session, { enabled = false, category = "all" } = {}) {
  const sessionId = session?.session_id;
  const key = sessionId ? `${sessionId}:${category}` : "";
  const activeKey = enabled ? key : "";
  const running = session?.status === "running";
  const isCurrentAccount = useAccountGuard();
  const [state, setState] = useState(() => empty(""));
  const [refreshVersion, setRefreshVersion] = useState(0);
  const currentKey = useRef(activeKey);
  const stateRef = useRef(state);
  const olderController = useRef(null);
  currentKey.current = activeKey;
  stateRef.current = state;

  const readHead = useCallback(async (signal) => {
    const valid = () => !signal.aborted && currentKey.current === key && isCurrentAccount();
    if (!valid()) return;
    setState((previous) => previous.key === key ? { ...previous, error: "", loadingMore: !!olderController.current && previous.loadingMore,
      status: previous.items.length ? "ready" : "loading" } : empty(key));
    try {
      const response = validateSessionLogResponse(
        await api.agentSessionLogs(sessionId, { category, signal }), sessionId);
      if (!valid()) return;
      setState((previous) => {
        const same = previous.key === key;
        const existing = same ? previous.items : [];
        // If polling missed an entire page, reset its cursor too. Otherwise
        // older pagination could silently skip the unseen interval.
        const overlap = response.items.some((item) => existing.some((old) => Number(old.id) === Number(item.id)));
        const gap = existing.length && response.items.length && response.has_more && !overlap
          && Number(response.items.at(-1).id) > Number(existing[0].id);
        const retainCursor = same && previous.olderLoaded && !gap;
        const items = mergeSessionLogItems(gap ? [] : existing, response.items);
        return { key, items, status: "ready", error: "", loadingMore: !!olderController.current && same && previous.loadingMore,
          retention: response.retention, olderLoaded: retainCursor,
          hasMore: items.length < 500 && (retainCursor ? previous.hasMore : !!response.has_more),
          nextBeforeId: retainCursor ? previous.nextBeforeId : response.next_before_id };
      });
      return { nextPollMs: 10000 };
    } catch (reason) {
      if (!valid()) return;
      setState((previous) => ({ ...(previous.key === key ? previous : empty(key)),
        status: "error", error: String(reason.message || reason) }));
      throw reason;
    }
  }, [category, isCurrentAccount, key, sessionId]);

  const trigger = useAdaptivePolling(readHead, { enabled: !!activeKey && running,
    intervalMs: 10000, maxIntervalMs: 30000, pollKey: activeKey });

  useEffect(() => {
    if (!activeKey || running) return undefined;
    const controller = new AbortController();
    readHead(controller.signal).catch(() => {});
    return () => controller.abort();
  }, [activeKey, readHead, refreshVersion, running]);

  useEffect(() => () => {
    olderController.current?.abort();
    olderController.current = null;
  }, [activeKey]);

  const refresh = useCallback(() => {
    if (!activeKey) return;
    if (running) trigger();
    else setRefreshVersion((value) => value + 1);
  }, [activeKey, running, trigger]);

  const loadMore = useCallback(async () => {
    const previous = stateRef.current;
    if (!activeKey || previous.key !== key || !previous.hasMore || !previous.nextBeforeId
      || olderController.current || !isCurrentAccount()) return;
    const controller = new AbortController();
    olderController.current = controller;
    const valid = () => !controller.signal.aborted && currentKey.current === key && isCurrentAccount();
    setState((value) => ({ ...value, loadingMore: true }));
    try {
      const response = validateSessionLogResponse(await api.agentSessionLogs(sessionId,
        { category, beforeId: previous.nextBeforeId, signal: controller.signal }), sessionId);
      if (!valid()) return;
      setState((value) => {
        if (value.key !== key || value.nextBeforeId !== previous.nextBeforeId) return { ...value, loadingMore: false };
        const items = mergeSessionLogItems(value.items, response.items);
        return { ...value, items, status: "ready", error: "", loadingMore: false, olderLoaded: true,
          hasMore: items.length < 500 && !!response.has_more, nextBeforeId: response.next_before_id };
      });
    } catch (reason) {
      if (valid()) setState((value) => value.key === key
        ? { ...value, status: "error", error: String(reason.message || reason), loadingMore: false } : value);
    } finally {
      if (olderController.current === controller) olderController.current = null;
    }
  }, [activeKey, category, isCurrentAccount, key, sessionId]);

  return { state: state.key === key ? state : empty(key), refresh, loadMore };
}
