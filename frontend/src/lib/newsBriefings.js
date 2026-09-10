const RETRY_MS = 30_000;
const MAX_RETRY_MS = 300_000;
const REQUEST_TIMEOUT_MS = 45_000;

export function hasKoreanText(value) {
  return /[가-힣]/.test(String(value || ""));
}

export function communitySummaryPresentation(item) {
  if (item?.content_type !== "community") return null;
  const text = String(item.community_summary || "").trim();
  if (item.community_summary_status === "ready" && hasKoreanText(text)) {
    return { status: "ready", label: item.community_summary_partial ? "본문 일부 요약" : "본문 요약", text };
  }
  return item.community_summary_status === "pending"
    ? { status: "pending", label: "본문 요약 중", text: "" }
    : { status: "unavailable", label: "본문 요약을 제공할 수 없어요.", text: "" };
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

export function hasPendingCommunitySummaries(payload) {
  return payload?.community_summaries?.status === "partial"
    || (payload?.items || []).some((item) => item?.content_type === "community"
      && item.community_summary_status === "pending");
}

export function hasPendingNewsWork(payload) {
  return hasPendingTranslation(payload) || hasPendingCommunitySummaries(payload);
}

export function newsRetryAfterSeconds(payload) {
  const delays = [
    hasPendingTranslation(payload) ? payload?.translation?.retry_after_seconds : null,
    hasPendingCommunitySummaries(payload) ? payload?.community_summaries?.retry_after_seconds : null,
  ].map(Number).filter((value) => Number.isFinite(value) && value > 0);
  return delays.length ? Math.min(...delays) : 30;
}

/**
 * 뉴스 응답 캐시 — 화면을 떠났다 돌아와도 마지막 응답을 바로 그린다.
 * 모듈 메모리 + sessionStorage 거울(새로고침에도 남는다). 저장소가 없거나 깨져 있어도 조용히 빈 캐시로 동작한다.
 */
export function createNewsCache({ storage = null, storageKey = "ggp_news_cache_v1", maxAgeMs = 12 * 60 * 60 * 1000, now = Date.now } = {}) {
  const entries = new Map();
  try {
    const raw = storage?.getItem(storageKey);
    if (raw) {
      for (const [key, entry] of Object.entries(JSON.parse(raw))) {
        if (entry && typeof entry === "object" && entry.data && Number.isFinite(entry.storedAt)) entries.set(key, entry);
      }
    }
  } catch { /* 저장소 접근 불가·손상 — 메모리 캐시만 쓴다 */ }
  const persist = () => {
    try { storage?.setItem(storageKey, JSON.stringify(Object.fromEntries(entries))); } catch { /* 용량 초과 등 — 메모리 캐시는 유지 */ }
  };
  return {
    get(key) {
      const entry = entries.get(key);
      if (!entry) return null;
      if (now() - entry.storedAt > maxAgeMs) { entries.delete(key); return null; }
      return entry;
    },
    set(key, data, storedAt = now()) { entries.set(key, { data, storedAt }); persist(); },
    isFresh(key, freshMs) { const entry = this.get(key); return Boolean(entry) && now() - entry.storedAt < freshMs; },
    clear() { entries.clear(); persist(); },
  };
}

const sessionStore = (() => { try { return typeof sessionStorage === "undefined" ? null : sessionStorage; } catch { return null; } })();
/** 앱 전체가 공유하는 뉴스 캐시 — 코인동향 화면이 라우트를 오가도 응답을 다시 받지 않는다. */
export const newsCache = createNewsCache({ storage: sessionStore });

/** 캐시로 만든 첫 상태 — 있으면 `success`(자료 포함), 없으면 `queued`. */
export function cachedNewsState(cache, key) {
  const hit = cache?.get(key);
  return hit ? { status: "success", data: hit.data, error: "" } : { status: "queued", data: null, error: "" };
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
  cache = null,
  freshMs = 0,
}) {
  // 캐시가 있으면 그 자료로 시작한다. 신선하고 남은 일(번역·요약 대기)이 없으면 요청 자체를 내지 않고,
  // 오래됐으면 보이는 채로 조용히 새로 받는다(status 는 자료가 있는 한 success).
  const records = new Map([...new Set(keys)].map((key) => {
    const hit = cache?.get(key) || null;
    const seeded = hit ? hit.data : null;
    const settled = Boolean(seeded) && now() - hit.storedAt < freshMs && !hasPendingNewsWork(seeded) && !seeded.stale;
    return [key, {
      status: seeded ? "success" : "queued", data: seeded, error: "", dueAt: settled ? null : 0,
      pendingAttempts: 0, failures: 0,
    }];
  }));
  const requests = new Map();
  const slots = Math.max(1, Math.trunc(concurrency) || 2);
  let active = false;
  let visible = true;
  let timer = null;
  const notify = (key, record) => {
    if (active) onChange(key, { status: record.status, data: record.data, error: record.error });
  };
  const retryDelay = (attempt, payload) => {
    const serverSeconds = newsRetryAfterSeconds(payload);
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
      cache?.set(key, record.data, now());
      record.pendingAttempts = hasPendingNewsWork(record.data) || record.data.stale
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
