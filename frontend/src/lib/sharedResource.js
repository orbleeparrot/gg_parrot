// One visible-page polling loop shared by all consumers of a public resource.
export function createSharedResource(load, { ttlMs, retryMs = 30_000, seed = null,
  isStale = () => false, onData = () => {}, now = Date.now,
  setTimer = setTimeout, clearTimer = clearTimeout,
  documentRef = typeof document === "undefined" ? null : document } = {}) {
  let state = { data: seed?.data || null, updatedAt: seed?.storedAt || 0, loading: !seed?.data, error: "" };
  let dueAt = seed?.data ? state.updatedAt + (isStale(seed.data) ? retryMs : ttlMs) : 0;
  let request = null;
  let timer = null;
  let failures = 0;
  const listeners = new Set();
  const visible = () => !documentRef?.hidden;
  const notify = () => listeners.forEach((listener) => listener());
  const clear = () => { if (timer != null) clearTimer(timer); timer = null; };
  const schedule = () => {
    clear();
    if (listeners.size && visible() && !request) timer = setTimer(() => { timer = null; void refresh().catch(() => {}); }, Math.max(0, dueAt - now()));
  };
  async function refresh(force = false) {
    if (request) return request.promise;
    if (!force && state.data && now() < dueAt) { schedule(); return state.data; }
    const entry = { controller: new AbortController(), promise: null };
    request = entry;
    state = { ...state, loading: !state.data, error: "" };
    notify();
    entry.promise = Promise.resolve().then(() => load(entry.controller.signal)).then((data) => {
      if (request !== entry || entry.controller.signal.aborted) return state.data;
      const previous = state.data;
      const same = previous && JSON.stringify(previous) === JSON.stringify(data);
      state = { data: same ? previous : data, updatedAt: now(), loading: false, error: "" };
      failures = 0;
      dueAt = now() + (isStale(data) ? retryMs : ttlMs);
      onData(state.data, state.updatedAt);
      return state.data;
    }).catch((reason) => {
      if (request !== entry || entry.controller.signal.aborted) return state.data;
      failures += 1;
      state = { ...state, loading: false, error: String(reason.message || reason) };
      dueAt = now() + Math.min(300_000, retryMs * 2 ** Math.min(failures - 1, 4));
      throw reason;
    }).finally(() => {
      if (request !== entry) return;
      request = null;
      notify();
      schedule();
    });
    return entry.promise;
  }
  const suspend = () => {
    clear();
    const old = request;
    request = null;
    old?.controller.abort();
  };
  const onVisible = () => { if (visible()) schedule(); else suspend(); };
  return {
    getSnapshot: () => state,
    refresh,
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) { documentRef?.addEventListener("visibilitychange", onVisible); schedule(); }
      return () => {
        listeners.delete(listener);
        if (!listeners.size) { documentRef?.removeEventListener("visibilitychange", onVisible); suspend(); }
      };
    },
    reset() {
      suspend(); failures = 0; dueAt = 0;
      state = { data: null, updatedAt: 0, loading: true, error: "" };
      notify(); schedule();
    },
  };
}
