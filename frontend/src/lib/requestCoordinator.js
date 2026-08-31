function abortError() {
  if (typeof DOMException !== "undefined") {
    return new DOMException("The operation was aborted", "AbortError");
  }
  const error = new Error("The operation was aborted");
  error.name = "AbortError";
  return error;
}

export function createRequestCoordinator() {
  const inflight = new Map();

  function subscribe(entry, signal) {
    const consumer = Symbol("request-consumer");
    entry.consumers.add(consumer);

    return new Promise((resolve, reject) => {
      let finished = false;
      const cleanup = () => {
        if (finished) return;
        finished = true;
        signal?.removeEventListener?.("abort", onAbort);
        entry.consumers.delete(consumer);
      };
      const onAbort = () => {
        cleanup();
        reject(abortError());
        if (!entry.settled && entry.consumers.size === 0) entry.controller.abort();
      };

      if (signal?.aborted) {
        onAbort();
        return;
      }
      signal?.addEventListener?.("abort", onAbort, { once: true });
      entry.promise.then(
        (value) => {
          if (finished) return;
          cleanup();
          resolve(value);
        },
        (reason) => {
          if (finished) return;
          cleanup();
          reject(reason);
        },
      );
    });
  }

  return {
    run(key, factory, { signal } = {}) {
      let entry = inflight.get(key);
      if (!entry) {
        const controller = new AbortController();
        entry = { controller, consumers: new Set(), settled: false, promise: null };
        try {
          entry.promise = Promise.resolve(factory(controller.signal));
        } catch (reason) {
          entry.promise = Promise.reject(reason);
        }
        inflight.set(key, entry);
        entry.promise.then(
          () => {
            entry.settled = true;
            if (inflight.get(key) === entry) inflight.delete(key);
          },
          () => {
            entry.settled = true;
            if (inflight.get(key) === entry) inflight.delete(key);
          },
        );
      }
      return subscribe(entry, signal);
    },
    clear() {
      for (const entry of inflight.values()) entry.controller.abort();
      inflight.clear();
    },
    size() {
      return inflight.size;
    },
  };
}
