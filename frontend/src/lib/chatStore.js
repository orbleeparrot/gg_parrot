import { CHAT_SEEN_STORAGE_KEY, countUnseen, isOwnMessage, latestMessageId, readSeenId, writeSeenId } from "./chatBadge.js";
import { mergeMessages } from "./chatFeed.js";

const feeds = new Map();
const listeners = new Map();
const responseTimes = new Map();
let listening = false;

export function getChatFeed(scope) {
  if (!feeds.has(scope)) {
    feeds.set(scope, {
      items: [], loaded: false, seenId: readSeenId(scope), latestId: 0,
      dayStartMs: 0, hasMore: false, oldestId: null, historyLoaded: false,
      unreadBase: 0, unreadSeenId: 0, unreadLatestId: 0, loadError: "", responseRevision: 0,
      pageLatestId: 0, countNeedsRefresh: false,
      metadataLoaded: false,
    });
  }
  return feeds.get(scope);
}

function update(scope, changes, response = false) {
  const current = getChatFeed(scope);
  if (!Object.entries(changes).some(([key, value]) => current[key] !== value)) return current;
  const next = { ...current, ...changes };
  if (response) next.responseRevision = current.responseRevision + 1;
  feeds.set(scope, next);
  listeners.get(scope)?.forEach((listener) => listener());
  return next;
}

export function observeChat(scope, listener) {
  if (!listeners.has(scope)) listeners.set(scope, new Set());
  listeners.get(scope).add(listener);
  if (!listening && typeof window !== "undefined") {
    window.addEventListener("storage", (event) => {
      const prefix = `${CHAT_SEEN_STORAGE_KEY}:`;
      if (!event.key?.startsWith(prefix) || event.newValue == null) return;
      const target = event.key.slice(prefix.length);
      const id = Number(event.newValue);
      if (!Number.isSafeInteger(id) || id < 0) return;
      const current = getChatFeed(target);
      const next = Math.max(current.seenId ?? 0, id);
      // Repair a stale tab's lower write, without echoing every storage event.
      if (id < next) writeSeenId(next, target);
      if (current.seenId == null || next > current.seenId) update(target, { seenId: next });
    });
    listening = true;
  }
  // A different tab may have advanced the cursor while this page was unmounted.
  const stored = readSeenId(scope);
  if (stored != null && stored > (getChatFeed(scope).seenId ?? -1)) update(scope, { seenId: stored });
  return () => listeners.get(scope)?.delete(listener);
}

export function markChatSeen(scope, id) {
  if (!Number.isSafeInteger(Number(id)) || Number(id) < 0) return;
  const current = getChatFeed(scope);
  const next = writeSeenId(Math.max(current.seenId ?? 0, Number(id)), scope);
  if (next != null && (current.seenId == null || next > current.seenId)) update(scope, { seenId: next });
}

export function receiveChat(scope, data, { older = false, beforeId } = {}) {
  const current = getChatFeed(scope);
  if (older && beforeId != null && Number(beforeId) !== Number(current.oldestId)) return;
  const dayStartMs = Math.max(current.dayStartMs, Number(data.day_start_ms) || 0);
  // A response captured before midnight must not restore yesterday's metadata.
  if (current.metadataLoaded && Number(data.day_start_ms) < current.dayStartMs) return;
  const newDay = dayStartMs > current.dayStartMs;
  const metadataOnly = data.mode === "metadata";
  const delta = data.mode === "delta";
  const reconcile = data.mode === "reconcile";
  const responseLatest = Number(data.latest_id) || latestMessageId(data.items);
  const responseTime = Number(data.snapshot_ms) || 0;
  const freshResponse = responseTime > (responseTimes.get(scope) || 0);
  if (!older && !metadataOnly && !reconcile && !newDay && !freshResponse && responseLatest < current.pageLatestId) return;
  if (responseTime) responseTimes.set(scope, Math.max(responseTimes.get(scope) || 0, responseTime));
  const incomingOldest = Math.min(...(data.items || []).map((item) => Number(item.id)));
  // After a long absence the newest page can be separated from our cache by
  // hundreds of rows. Restart its backwards pagination at the new page; a POST
  // ahead of that GET is not proof that the intervening history was fetched.
  const historyGap = !older && !delta && !reconcile && !metadataOnly && !newDay && current.loaded && data.items?.length > 0 && data.has_more && incomingOldest > current.pageLatestId;
  let base = historyGap ? current.items.filter((item) => Number(item.id) >= incomingOldest) : current.items;
  if (reconcile && data.missing_ids?.length) {
    const missing = new Set(data.missing_ids.map(Number));
    base = base.filter((item) => !missing.has(Number(item.id)));
  }
  const items = !newDay && !data.items?.length && base === current.items ? current.items : mergeMessages(base, data.items, dayStartMs);
  const latestId = Math.max(newDay || freshResponse ? 0 : current.latestId, Number(data.latest_id) || 0, latestMessageId(items));
  const responseSeen = data.seen_id == null ? null : Number(data.seen_id);
  const seenId = writeSeenId(Math.max(current.seenId ?? responseSeen ?? latestId, responseSeen ?? 0), scope);
  const responseCursor = responseSeen ?? seenId;
  // Counts have two coordinates: message watermark and read cursor. A later
  // message watermark with an older reader cursor is still an obsolete count.
  const useMetadata = newDay || !current.metadataLoaded || ((freshResponse || responseLatest >= current.unreadLatestId) && responseCursor >= current.unreadSeenId);
  const metadata = useMetadata ? {
    unreadBase: Math.max(0, Number(data.unseen_count) || 0),
    unreadSeenId: responseSeen ?? seenId,
    unreadLatestId: responseLatest,
  } : {};
  const pageLatestId = Math.max(newDay ? 0 : current.pageLatestId,
    older || metadataOnly || reconcile ? 0 : delta ? Number(data.fetched_through_id) || 0 : responseLatest);
  return update(scope, {
    items, loaded: metadataOnly ? current.loaded && !newDay : reconcile ? current.loaded : true,
    metadataLoaded: true, seenId, latestId, dayStartMs, loadError: "", ...metadata,
    pageLatestId,
    countNeedsRefresh: useMetadata
      ? !metadataOnly && !delta && !reconcile && responseLatest !== pageLatestId
      : current.countNeedsRefresh || responseLatest > current.unreadLatestId,
    oldestId: items[0]?.id ?? null,
    hasMore: metadataOnly || delta || reconcile ? (newDay ? false : current.hasMore) : older || newDay || historyGap || !current.historyLoaded ? !!data.has_more : current.hasMore,
    historyLoaded: newDay || historyGap ? older : current.historyLoaded || older,
  }, true);
}

export function receiveChatPost(scope, message) {
  const current = getChatFeed(scope);
  if (!message) return;
  const items = mergeMessages(current.items, [message], current.dayStartMs);
  return update(scope, { items, latestId: Math.max(current.latestId, latestMessageId(items)) });
}

export function setChatLoadError(scope, error) {
  update(scope, { loadError: String(error?.message || error || "채팅을 불러오지 못했어요.") });
}

export function chatUnseenCount(feed, userId = null) {
  if (feed.seenId == null) return 0;
  const local = countUnseen(feed.items, feed.seenId, userId);
  if (feed.seenId >= feed.unreadLatestId) return local;
  const readSinceSnapshot = feed.items.filter((item) => Number(item.id) > feed.unreadSeenId && Number(item.id) <= feed.seenId && !isOwnMessage(item, userId)).length;
  const arrivedSinceSnapshot = feed.items.filter((item) => Number(item.id) > feed.unreadLatestId && Number(item.id) > feed.seenId && !isOwnMessage(item, userId)).length;
  return Math.max(local, feed.unreadBase - readSinceSnapshot + arrivedSinceSnapshot, 0);
}

// Only a latest-page GET establishes which messages up to this ID were loaded.
// An own POST or an older page's global metadata can reveal a later ID while
// intervening messages are still missing from the rendered list.
export function visibleChatReadId(feed) {
  return Math.min(latestMessageId(feed.items), feed.pageLatestId);
}
