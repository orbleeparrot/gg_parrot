// Correlation is an explicit server fact. Never guess a match from a title,
// timestamp, price, position or a browser-calculated strategy marker.
export function positiveEventId(value) {
  if (typeof value !== "number" && (typeof value !== "string" || !/^\d+$/.test(value))) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) && number > 0 ? number : null;
}

export function activityLogLink(sessionId, eventId) {
  const session = positiveEventId(sessionId), event = positiveEventId(eventId);
  return session && event ? `/agents?session=${session}&panel=logs&event=${event}` : "";
}

export function activityEventId(event, sessionId) {
  return positiveEventId(event?.activitySessionId) === positiveEventId(sessionId)
    ? positiveEventId(event?.activityEventId) : null;
}

export function newObservationId() {
  return globalThis.crypto?.randomUUID?.() || "";
}

export function observationForEvent(event) {
  if (!event || event.isNewsArticle || event.module === "runner_log" || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(event.observationId || "")) return null;
  let name;
  if (event.connectionIncident || event.module === "position_news") name = "connection";
  else if (event.module === "execution") name = "execution_state";
  else if (event.module === "risk") name = "risk";
  else if (event.module === "strategy") name = "strategy_reference";
  else if (event.module === "whale_activity") name = String(event.id).startsWith("onchain:") ? "onchain_balance" : "whale_trade";
  else return null;
  const observedAt = Number(event.observedAt);
  if (!Number.isFinite(observedAt) || observedAt <= 0 || !Number.isFinite(new Date(observedAt).getTime())) return null;
  const scalarText = (value, limit) => typeof value === "string" ? value.slice(0, limit) : "";
  return { client_event_id: event.observationId, event_name: `browser.${name}_observed`,
    observed_at: new Date(observedAt).toISOString(), details: {
      module: scalarText(event.module, 40), observation_type: name,
      state: event.resolvedAt ? "resolved" : scalarText(String(event.conditionValue || "observed"), 80),
      title: scalarText(event.title, 500), summary: scalarText(event.summary, 1200),
      source_label: scalarText(event.sourceLabel, 120), reference_id: scalarText(String(event.id || ""), 200),
      original_occurred_at: scalarText(String(event.occurredAt || ""), 80),
      resolved: !!event.resolvedAt, condition_key: scalarText(event.conditionKey, 120),
    } };
}

function wait(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new Error("aborted")); return; }
    const abort = () => { clearTimeout(timer); reject(new Error("aborted")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export function createObservationSender({ send, onStatus = () => {}, wait: pause = wait }) {
  const entries = new Map();
  let stopped = false;
  function submit(sessionId, payload, { retry = false } = {}) {
    const sid = positiveEventId(sessionId), key = payload?.client_event_id;
    if (stopped || !sid || !key) return Promise.resolve();
    const identity = `${sid}:${key}`, previous = entries.get(identity);
    if (previous && (!retry || previous.status !== "error")) return previous.promise;
    const controller = new AbortController();
    // A UUID identifies its FIRST observed facts, even after a card is updated
    // and the user manually retries a failed save. A genuinely new observation
    // must have a new UUID, never new facts under an existing event identity.
    const snapshot = previous?.snapshot || JSON.parse(JSON.stringify(payload));
    const entry = { controller, status: "saving", promise: null, snapshot };
    entries.set(identity, entry);
    const announce = (status, facts = {}) => {
      entry.status = status;
      if (!stopped && !controller.signal.aborted) onStatus({ sessionId: sid, key, status, ...facts });
    };
    announce("saving");
    entry.promise = (async () => {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          // The transport receives a copy so it cannot mutate the snapshot
          // reused by subsequent automatic OR manual attempts.
          const result = await send(sid, JSON.parse(JSON.stringify(snapshot)), { signal: controller.signal });
          const id = positiveEventId(result?.event_id), canonical = result?.event;
          if (!result?.ok || !id || positiveEventId(canonical?.id) !== id
            || positiveEventId(canonical?.session_id) !== sid || canonical?.event_key !== `browser:${key}`
            || canonical?.component !== "browser_observation" || canonical?.event_name !== snapshot.event_name) {
            throw new Error("관측 원본의 사건 ID를 확인하지 못했어요.");
          }
          announce("ready", { eventId: id });
          return;
        } catch (error) {
          if (stopped || controller.signal.aborted) return;
          if (attempt === 1 || error?.status >= 400 && error.status < 500 && error.status !== 429) {
            announce("error", { error: "관측 기록 저장 실패 · 실제 주문/체결 실패를 뜻하지 않아요." });
            return;
          }
          try { await pause(1200, controller.signal); } catch { return; }
        }
      }
    })();
    // A visible 200-incident timeline may retain both its initial and recovery
    // UUIDs. Keep both phases so normal rendering cannot restart retired sends.
    while (entries.size > 400) {
      const oldest = entries.keys().next().value;
      entries.get(oldest).controller.abort(); entries.delete(oldest);
    }
    return entry.promise;
  }
  return { submit, stop() { stopped = true; for (const item of entries.values()) item.controller.abort(); entries.clear(); } };
}
