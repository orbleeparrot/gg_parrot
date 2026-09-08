const RETRY_MS = 30_000;
const MAX_RETRY_MS = 300_000;
const REQUEST_TIMEOUT_MS = 45_000;

export function hasKoreanText(value) {
  return /[가-힣]/.test(String(value || ""));
}

export function communityPostIdentity(item) {
  if (item?.content_type !== "community") return null;
  const post = String(item.community_post_id || "").trim() || String(item.url || "").trim();
  if (!post) return null;
  const source = String(item.source || "Binance Square").normalize("NFKC").trim().toLowerCase();
  return `community:${JSON.stringify([source, post])}`;
}

export function newsSourceLabel(item) {
  if (item?.content_type !== "community") return item?.source || "출처 미상";
  return ["커뮤니티", item.source || "Binance Square", String(item.author || "").trim() || "작성자 미상"].join(" · ");
}

export function newsPublishedLabel(item) {
  if (item?.published_display) return item.published_display;
  const published = typeof item?.published === "number" ? item.published : Date.parse(item?.published || "");
  const date = new Date(published + 9 * 60 * 60 * 1000);
  if (!Number.isFinite(published) || published <= 0 || !Number.isFinite(date.getTime())) return "게시일 확인 불가";
  return `${date.toISOString().slice(0, 16).replaceAll("-", ".").replace("T", " ")} KST`;
}

export function historicalNewsLabel(item) {
  if (!item?.is_historical) return "";
  const published = typeof item.published === "number" ? item.published : Date.parse(item.published || "");
  const date = new Date(published + 9 * 60 * 60 * 1000);
  const label = Number.isFinite(published) && published > 0 && Number.isFinite(date.getTime())
    ? date.toISOString().slice(0, 10).replaceAll("-", ".") : "게시일 확인 불가";
  return `${item.content_type === "community" ? "과거 게시글" : "과거 기사"} · ${label}`;
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
  setTimer = (fn, delay) => setTimeout(fn, delay),
  clearTimer = (id) => clearTimeout(id),
  requestTimeoutMs = REQUEST_TIMEOUT_MS,
}) {
  const records = new Map([...new Set(keys)].map((key) => [key, {
    status: "queued", data: null, error: "", dueAt: 0,
    pendingAttempts: 0, failures: 0,
  }]));
  const requests = new Map();
  const slots = Math.max(1, Math.trunc(concurrency) || 2);
  let active = false;
  let visible = true;
  let timer = null;
  const notify = (key, record) => {
    if (active) onChange(key, { status: record.status, data: record.data, error: record.error });
  };
  const retryDelay = (attempt, payload) => {
    const serverSeconds = Number(payload?.translation?.retry_after_seconds);
    const serverDelay = Number.isFinite(serverSeconds) && serverSeconds > 0
      ? Math.min(3_600_000, serverSeconds * 1000) : RETRY_MS;
    return Math.max(serverDelay, Math.min(MAX_RETRY_MS, RETRY_MS * (2 ** Math.min(attempt - 1, 4))));
  };

  const clearScheduled = () => {
    if (timer !== null) clearTimer(timer);
    timer = null;
  };
  const schedule = (delay) => {
    clearScheduled();
    if (active && visible) timer = setTimer(pump, Math.max(0, delay));
  };

  function startRequest(key, record) {
    const controller = new AbortController();
    const request = { controller, timer: null, cancel: null };
    const interrupted = new Promise((_resolve, reject) => {
      request.cancel = (reason) => {
        // Settle the timeout first so fetch's AbortError cannot turn it into
        // an immediate retry. Visibility cancellation uses AbortError itself.
        reject(reason);
        controller.abort();
      };
    });
    requests.set(key, request);
    record.status = record.data ? "success" : "loading";
    record.loading = true;
    record.error = "";
    notify(key, record);
    request.timer = setTimer(() => request.cancel(Object.assign(
      new Error("뉴스 응답이 지연되어 잠시 후 다시 확인해요."), { name: "TimeoutError" },
    )), requestTimeoutMs);
    const current = () => active && visible && requests.get(key) === request;
    const response = Promise.resolve().then(() => {
      if (controller.signal.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      return load(key, controller.signal);
    });
    // One stalled source must not hold the other slot or the whole retry batch.
    Promise.race([response, interrupted]).then((payload) => {
      if (!current()) return;
      record.data = prepareNewsResponse(payload);
      record.status = "success";
      record.failures = 0;
      record.pendingAttempts = hasPendingTranslation(record.data) || record.data.stale
        ? record.pendingAttempts + 1 : 0;
      record.dueAt = record.pendingAttempts
        ? now() + retryDelay(record.pendingAttempts, record.data) : null;
    }).catch((reason) => {
      if (!current()) return;
      if (reason?.name === "AbortError") {
        record.status = record.data ? "success" : "queued";
        return;
      }
      record.status = "error";
      record.error = reason instanceof Error ? reason.message : String(reason);
      record.failures += 1;
      const transient = !reason?.status || reason.status === 429 || reason.status >= 500;
      record.dueAt = transient ? now() + retryDelay(record.failures) : null;
    }).finally(() => {
      clearTimer(request.timer);
      if (requests.get(key) !== request) return;
      requests.delete(key);
      record.loading = false;
      notify(key, record);
      pump();
    });
  }

  function pump() {
    clearScheduled();
    if (!active || !visible) return;
    const due = [...records]
      .filter(([, record]) => !record.loading && record.dueAt !== null && record.dueAt <= now())
      .sort((a, b) => a[1].dueAt - b[1].dueAt);
    for (const [key, record] of due) {
      if (requests.size >= slots) break;
      startRequest(key, record);
    }
    if (requests.size >= slots) return;
    const deadlines = [...records.values()]
      .filter((record) => !record.loading && record.dueAt !== null)
      .map((record) => record.dueAt);
    if (deadlines.length) schedule(Math.min(...deadlines) - now());
  }

  function cancelRequests() {
    for (const [key, request] of requests) {
      requests.delete(key);
      clearTimer(request.timer);
      request.cancel(Object.assign(new Error("aborted"), { name: "AbortError" }));
      const record = records.get(key);
      record.loading = false;
      record.status = record.data ? "success" : "queued";
      notify(key, record);
    }
  }

  return {
    start() { if (!active) { active = true; schedule(0); } },
    stop() { active = false; clearScheduled(); cancelRequests(); },
    setVisible(nextVisible) {
      if (visible === !!nextVisible) return;
      visible = !!nextVisible;
      clearScheduled();
      if (visible) schedule(0);
      else cancelRequests();
    },
    retry(key) {
      const record = records.get(key);
      if (!record || record.loading || !active) return;
      record.dueAt = 0;
      record.failures = 0;
      schedule(0);
    },
  };
}
