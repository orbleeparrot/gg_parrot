import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { useAccountGuard } from "../../lib/auth.js";
import { createNewsReceiptSender } from "./newsReceipts.js";
const EMPTY_LINKS = Object.freeze({});

export function useNewsReceipt(session, data) {
  const isCurrentAccount = useAccountGuard();
  const [state, setState] = useState(null);
  const [links, setLinks] = useState({ sessionId: null, values: EMPTY_LINKS });
  const current = useRef(null);
  const lastReceipt = useRef(null);
  const senderRef = useRef(null);
  const sessionId = session?.session_id;
  const running = session?.status === "running";
  const key = data?.receipt_key;
  if (!running || lastReceipt.current?.sessionId !== sessionId) lastReceipt.current = null;
  if (running && key && data?.receipt_token) {
    lastReceipt.current = { sessionId, key, token: data.receipt_token };
  }
  // An empty delta carries no new receipt, but must not erase a failed save
  // and its manual retry capability. Never transfer it to another session.
  current.current = { ...lastReceipt.current, sessionId, running };
  useEffect(() => {
    // StrictMode replays effect setup/cleanup in development. An aborted sender
    // must never be reused by the next setup, including same-account remounts.
    const sender = createNewsReceiptSender({
      send: (id, token, options) => api.agentNewsReceipt(id, token, options),
      onStatus: (next) => {
        if (!isCurrentAccount() || Number(current.current.sessionId) !== next.sessionId) return;
        if (next.status === "ready" && next.link) setLinks((previous) => ({ sessionId: next.sessionId,
          values: Object.fromEntries(Object.entries({ ...(previous.sessionId === next.sessionId ? previous.values : {}),
            [next.key]: next.link }).slice(-200)) }));
        if (current.current.key === next.key) setState(next);
      },
    });
    senderRef.current = sender;
    return () => {
      sender.stop();
      if (senderRef.current === sender) senderRef.current = null;
    };
  }, [isCurrentAccount]);
  useEffect(() => {
    if (running && isCurrentAccount()) senderRef.current?.submit(current.current);
  }, [isCurrentAccount, key, running, sessionId]);
  const retry = useCallback(() => {
    if (current.current.running && isCurrentAccount()) senderRef.current?.submit(current.current, { retry: true });
  }, [isCurrentAccount]);
  return { state: running && Number(state?.sessionId) === Number(sessionId)
    && state?.key === current.current.key ? state : null,
    links: Number(links.sessionId) === Number(sessionId) ? links.values : EMPTY_LINKS, retry };
}
