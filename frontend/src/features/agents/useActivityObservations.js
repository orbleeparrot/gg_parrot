import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api.js";
import { useAccountGuard } from "../../lib/auth.js";
import { createObservationSender, observationForEvent } from "./activityEvidence.js";

export function useActivityObservations(sessionId, events) {
  const isCurrentAccount = useAccountGuard();
  const [records, setRecords] = useState({ sessionId: null, values: {} });
  const current = useRef(sessionId), senderRef = useRef(null);
  current.current = sessionId;
  useEffect(() => {
    const sender = createObservationSender({ send: (sid, payload, options) => api.agentObservation(sid, payload, options),
      onStatus: (next) => {
        if (!isCurrentAccount() || Number(current.current) !== next.sessionId) return;
        setRecords((previous) => ({ sessionId: next.sessionId, values: Object.fromEntries(
          Object.entries({ ...(previous.sessionId === next.sessionId ? previous.values : {}), [next.key]: next }).slice(-400)) }));
      } });
    senderRef.current = sender;
    return () => { sender.stop(); if (senderRef.current === sender) senderRef.current = null; };
  }, [isCurrentAccount, sessionId]);
  useEffect(() => {
    if (!sessionId || !isCurrentAccount()) return;
    for (const event of events) {
      const payload = observationForEvent(event);
      if (payload) senderRef.current?.submit(sessionId, payload);
    }
  }, [events, isCurrentAccount, sessionId]);
  const retry = useCallback((event) => {
    const payload = observationForEvent(event);
    if (payload && isCurrentAccount()) senderRef.current?.submit(current.current, payload, { retry: true });
  }, [isCurrentAccount]);
  return { records: records.sessionId === Number(sessionId) ? records.values : {}, retry };
}
