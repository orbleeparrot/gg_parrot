const THRESHOLDS = {
  CLS: [0.1, 0.25],
  INP: [200, 500],
  LCP: [2_500, 4_000],
  FCP: [1_800, 3_000],
  TTFB: [800, 1_800],
};

export function ratingFor(name, value) {
  const [good, poor] = THRESHOLDS[name] || [Number.POSITIVE_INFINITY, Number.POSITIVE_INFINITY];
  if (value <= good) return "good";
  if (value <= poor) return "needs-improvement";
  return "poor";
}

function identifierSegment(segment) {
  return (
    /^\d+$/.test(segment)
    || /^[0-9a-f]{8}-[0-9a-f-]{27,}$/i.test(segment)
    || /^[0-9a-f]{16,}$/i.test(segment)
  );
}

export function sanitizeRoute(rawPath) {
  const path = String(rawPath || "/").split(/[?#]/, 1)[0] || "/";
  const normalized = `/${path.replace(/^\/+/, "")}`
    .split("/")
    .map((segment) => (identifierSegment(segment) ? ":id" : segment))
    .join("/");
  return normalized.slice(0, 200) || "/";
}

export function createRumTransport({
  navigatorObject = globalThis.navigator,
  fetchFn = globalThis.fetch,
  endpoint = "/api/observability/rum",
} = {}) {
  return async (payload) => {
    const body = JSON.stringify(payload);
    if (typeof navigatorObject?.sendBeacon === "function") {
      const accepted = navigatorObject.sendBeacon(
        endpoint,
        new Blob([body], { type: "application/json" }),
      );
      if (accepted) return true;
    }
    if (typeof fetchFn !== "function") return false;
    await fetchFn(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
      credentials: "omit",
    });
    return true;
  };
}

function percentileInp(interactions) {
  const values = [...interactions.values()].sort((a, b) => b - a);
  if (!values.length) return null;
  // Approximate the 98th percentile used by the web-vitals INP algorithm.
  return values[Math.min(values.length - 1, Math.floor(values.length / 50))];
}

function pageId(windowObject) {
  return windowObject.crypto?.randomUUID?.()
    || `page-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

export function startRum({
  windowObject = globalThis.window,
  documentObject = globalThis.document,
  PerformanceObserverClass = globalThis.PerformanceObserver,
  performanceObject = globalThis.performance,
  navigatorObject = globalThis.navigator,
  random = Math.random,
  sampleRate = Number(import.meta.env?.VITE_RUM_SAMPLE_RATE ?? (import.meta.env?.DEV ? 1 : 0.1)),
} = {}) {
  const rate = Math.max(0, Math.min(1, Number.isFinite(sampleRate) ? sampleRate : 0.1));
  if (!windowObject || !documentObject || !PerformanceObserverClass || random() >= rate) {
    return () => {};
  }

  const metrics = new Map();
  const sent = new Map();
  const observers = [];
  const interactions = new Map();
  const transport = createRumTransport({ navigatorObject });
  const id = pageId(windowObject);
  const route = sanitizeRoute(windowObject.location?.pathname || "/");
  let clsSessionValue = 0;
  let clsSessionStart = 0;
  let clsLastEntry = 0;
  let clsMax = 0;

  const setMetric = (name, value) => {
    if (!Number.isFinite(value) || value < 0) return;
    metrics.set(name, value);
  };
  setMetric("CLS", 0);

  const observe = (options, callback) => {
    try {
      const observer = new PerformanceObserverClass(callback);
      observer.observe(options);
      observers.push({ callback, observer });
    } catch (_) {
      // Older browsers omit some entry types; other metrics still work.
    }
  };

  observe({ type: "layout-shift", buffered: true }, (list) => {
    for (const entry of list.getEntries()) {
      if (entry.hadRecentInput) continue;
      const outsideSession = entry.startTime - clsLastEntry > 1_000
        || entry.startTime - clsSessionStart > 5_000;
      if (outsideSession) {
        clsSessionValue = entry.value;
        clsSessionStart = entry.startTime;
      } else {
        clsSessionValue += entry.value;
      }
      clsLastEntry = entry.startTime;
      clsMax = Math.max(clsMax, clsSessionValue);
      setMetric("CLS", clsMax);
    }
  });

  observe({ type: "largest-contentful-paint", buffered: true }, (list) => {
    const entries = list.getEntries();
    const latest = entries[entries.length - 1];
    if (latest) setMetric("LCP", latest.startTime);
  });

  observe({ type: "event", buffered: true, durationThreshold: 40 }, (list) => {
    for (const entry of list.getEntries()) {
      if (!entry.interactionId) continue;
      interactions.set(
        entry.interactionId,
        Math.max(interactions.get(entry.interactionId) || 0, entry.duration),
      );
    }
    const inp = percentileInp(interactions);
    if (inp != null) setMetric("INP", inp);
  });

  observe({ type: "first-input", buffered: true }, (list) => {
    const firstInput = list.getEntries()[0];
    if (firstInput && !metrics.has("INP")) setMetric("INP", firstInput.duration);
  });

  observe({ type: "paint", buffered: true }, (list) => {
    const fcp = list.getEntries().find((entry) => entry.name === "first-contentful-paint");
    if (fcp) setMetric("FCP", fcp.startTime);
  });

  const navigation = performanceObject?.getEntriesByType?.("navigation")?.[0];
  if (navigation?.responseStart != null) setMetric("TTFB", navigation.responseStart);

  const drainObservers = () => {
    for (const { callback, observer } of observers) {
      try {
        const entries = observer.takeRecords?.() || [];
        if (entries.length) callback({ getEntries: () => entries });
      } catch (_) {
        // A metric observer may disappear while the page is being frozen.
      }
    }
  };

  const flush = () => {
    drainObservers();
    const changed = [];
    for (const [name, rawValue] of metrics) {
      const value = name === "CLS"
        ? Number(rawValue.toFixed(4))
        : Number(rawValue.toFixed(2));
      if (sent.get(name) === value) continue;
      sent.set(name, value);
      changed.push({ name, value, rating: ratingFor(name, value) });
    }
    if (!changed.length) return;
    void transport({
      page_id: id,
      route,
      metrics: changed,
    }).catch(() => {});
  };

  const onVisibility = () => {
    if (documentObject.visibilityState === "hidden") flush();
  };
  documentObject.addEventListener("visibilitychange", onVisibility, true);
  windowObject.addEventListener("pagehide", flush, true);

  return () => {
    flush();
    observers.forEach(({ observer }) => observer.disconnect());
    documentObject.removeEventListener("visibilitychange", onVisibility, true);
    windowObject.removeEventListener("pagehide", flush, true);
  };
}
