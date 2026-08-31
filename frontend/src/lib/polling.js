function isAbortError(reason) {
  return reason?.name === "AbortError";
}

export function createAdaptivePoller({
  task,
  intervalMs,
  maxIntervalMs = Math.max(intervalMs, intervalMs * 16),
  jitterRatio = 0.1,
  random = Math.random,
  setTimer = (fn, delay) => setTimeout(fn, delay),
  clearTimer = (id) => clearTimeout(id),
  onError = () => {},
}) {
  let active = false;
  let visible = true;
  let running = false;
  let failures = 0;
  let timer = null;
  let controller = null;

  const clearScheduled = () => {
    if (timer == null) return;
    clearTimer(timer);
    timer = null;
  };

  const schedule = (delay) => {
    clearScheduled();
    if (!active || !visible) return;
    timer = setTimer(() => {
      timer = null;
      void run();
    }, Math.max(0, delay));
  };

  const retryDelay = () => {
    const base = Math.min(maxIntervalMs, intervalMs * (2 ** failures));
    if (!jitterRatio) return base;
    const jitter = base * jitterRatio * ((random() * 2) - 1);
    return Math.max(0, Math.round(base + jitter));
  };

  const run = async () => {
    if (!active || !visible || running) return;
    running = true;
    controller = new AbortController();
    let aborted = false;
    try {
      await task({ signal: controller.signal });
      failures = 0;
    } catch (reason) {
      aborted = isAbortError(reason) || controller.signal.aborted;
      if (!aborted) {
        failures += 1;
        onError(reason, failures);
      }
    } finally {
      running = false;
      controller = null;
      if (active && visible) schedule(aborted ? 0 : retryDelay());
    }
  };

  return {
    start({ immediate = true } = {}) {
      if (active) return;
      active = true;
      schedule(immediate ? 0 : intervalMs);
    },
    stop() {
      active = false;
      clearScheduled();
      controller?.abort();
    },
    trigger() {
      if (!active || !visible || running) return;
      schedule(0);
    },
    setVisible(nextVisible) {
      const next = !!nextVisible;
      if (visible === next) return;
      visible = next;
      clearScheduled();
      if (!visible) {
        controller?.abort();
        return;
      }
      failures = 0;
      schedule(0);
    },
    state() {
      return { active, visible, running, failures };
    },
  };
}
