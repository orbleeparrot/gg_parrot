import { buildAgentEvents } from "./registry.js";

const MAX_EVENTS = 200;
const MAX_SEEN = 2000;

export function emptyActivityTimeline(sessionId) {
  return { sessionId, session: null, events: [], seen: {}, conditions: {}, sequence: 0 };
}

function occurredAt(value, receivedAt) {
  const parsed = typeof value === "number" ? value : Date.parse(value || "");
  return Number.isFinite(parsed) && parsed > 0 ? parsed : receivedAt;
}

// Poll responses are observations, not chat messages. Remember incidents and
// article/trade identities across missing or reordered responses.
export function advanceActivityTimeline(previous, context) {
  const state = previous?.sessionId === context.session?.session_id
    ? previous : emptyActivityTimeline(context.session?.session_id);
  const receivedAt = context.receivedAt || Date.now();
  const candidates = buildAgentEvents({ ...context, receivedAt,
    previousSession: state.session, alertConditions: state.conditions });
  const conditions = {};
  const seen = { ...state.seen };
  let events = state.events;
  let sequence = state.sequence;
  for (const candidate of candidates) {
    if (candidate.conditionKey) {
      conditions[candidate.conditionKey] = candidate.conditionValue;
      if (state.conditions[candidate.conditionKey] === candidate.conditionValue) continue;
    }
    if (candidate.silent || !candidate.id) continue;
    const repeatable = candidate.repeatable || (candidate.conditionKey && candidate.repeatable !== false);
    const id = repeatable ? `${candidate.id}:occurrence:${++sequence}` : candidate.id;
    if (Object.hasOwn(seen, id)) {
      // Translation may improve an article without changing its time/order
      // or announcing the same article again.
      const index = events.findIndex((event) => event.id === id);
      if (index >= 0) {
        const updated = { ...candidate, id, occurredAt: events[index].occurredAt };
        if (JSON.stringify(updated) !== JSON.stringify(events[index])) {
          events = events.map((event, i) => i === index ? updated : event);
        }
      }
      continue;
    }
    seen[id] = receivedAt;
    events = [...events, { ...candidate, id, occurredAt: occurredAt(candidate.occurredAt, receivedAt) }];
  }
  const seenIds = Object.keys(seen);
  for (const id of seenIds.slice(0, Math.max(0, seenIds.length - MAX_SEEN))) delete seen[id];
  if (events.length > MAX_EVENTS) events = events.slice(-MAX_EVENTS);
  return { sessionId: context.session?.session_id, session: context.session,
    events, seen, conditions, sequence };
}
