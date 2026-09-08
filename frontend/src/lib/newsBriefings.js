import { createAdaptivePoller } from "./polling.js";

const RETRY_MS = 30_000;
const MAX_RETRY_MS = 300_000;

export function hasKoreanText(value) {
  return /[가-힣]/.test(String(value || ""));
}

export function prepareNewsResponse(payload = {}) {
  const sourceItems = Array.isArray(payload.items) ? payload.items : [];
  // The API validates full translations. This also protects mixed-version
  // deployments where an old response can still contain an English headline.
  const items = sourceItems.filter((item) => item && hasKoreanText(item.title));
  const pendingCount = Math.max(
    Number(payload.translation?.pending_count) || 0,
    sourceItems.length - items.length,
  );
  const partial = payload.translation?.status === "partial" || pendingCount > 0;
  return {
    ...payload,
    items,
    overview: hasKoreanText(payload.overview) ? payload.overview : null,
    translation: {
      ...payload.translation,
      status: partial ? "partial" : "ready",
      pending_count: pendingCount,
    },
  };
}

export function hasPendingTranslation(payload) {
  return payload?.translation?.status === "partial";
}

export function createNewsBriefingQueue({
  keys,
  load,
  onChange,
  concurrency = 2,
  now = Date.now,
  setTimer,
  clearTimer,
}) {
  const records = new Map([...new Set(keys)].map((key) => [key, {
    status: "queued", data: null, error: "", dueAt: 0,
    pendingAttempts: 0, failures: 0,
  }]));
  let active = false;
  const notify = (key, record) => {
    if (active) onChange(key, { status: record.status, data: record.data, error: record.error });
  };
  const retryDelay = (attempt, payload) => {
    const serverSeconds = Number(payload?.translation?.retry_after_seconds);
    const serverDelay = Number.isFinite(serverSeconds) && serverSeconds > 0
      ? Math.min(3_600_000, serverSeconds * 1000) : RETRY_MS;
    return Math.max(serverDelay, Math.min(MAX_RETRY_MS, RETRY_MS * (2 ** Math.min(attempt - 1, 4))));
  };

  const poller = createAdaptivePoller({
    intervalMs: RETRY_MS,
    maxIntervalMs: MAX_RETRY_MS,
    ...(setTimer ? { setTimer } : {}),
    ...(clearTimer ? { clearTimer } : {}),
    task: async ({ signal }) => {
      const due = [...records].filter(([, record]) => record.dueAt !== null && record.dueAt <= now());
      let cursor = 0;
      async function worker() {
        while (cursor < due.length && active && !signal.aborted) {
          const [key, record] = due[cursor++];
          record.status = record.data ? "success" : "loading";
          record.loading = true;
          record.error = "";
          notify(key, record);
          try {
            const response = await load(key, signal);
            if (!active || signal.aborted) continue;
            record.data = prepareNewsResponse(response);
            record.status = "success";
            record.failures = 0;
            record.pendingAttempts = hasPendingTranslation(record.data) || record.data.stale
              ? record.pendingAttempts + 1 : 0;
            record.dueAt = record.pendingAttempts
              ? now() + retryDelay(record.pendingAttempts, record.data) : null;
          } catch (reason) {
            if (signal.aborted || reason?.name === "AbortError") {
              record.status = record.data ? "success" : "queued";
              continue;
            }
            record.status = "error";
            record.error = reason instanceof Error ? reason.message : String(reason);
            record.failures += 1;
            const transient = !reason?.status || reason.status === 429 || reason.status >= 500;
            record.dueAt = transient
              ? now() + retryDelay(record.failures) : null;
          } finally {
            record.loading = false;
            notify(key, record);
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(concurrency, due.length) }, () => worker()));
      const deadlines = [...records.values()].map((record) => record.dueAt).filter((value) => value !== null);
      return { nextPollMs: deadlines.length ? Math.max(0, Math.min(...deadlines) - now()) : null };
    },
  });

  return {
    start() { active = true; poller.start(); },
    stop() { active = false; poller.stop(); },
    setVisible(visible) { poller.setVisible(visible); },
    retry(key) {
      const record = records.get(key);
      if (!record || record.loading || !active) return;
      record.dueAt = 0;
      record.failures = 0;
      poller.trigger();
    },
  };
}
