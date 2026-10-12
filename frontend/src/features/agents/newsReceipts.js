// A receipt acknowledges a server response, never an order or a human read.
function waitForRetry(milliseconds, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(Object.assign(new Error("Request aborted"), { name: "AbortError" }));
    };
    if (signal.aborted) { reject(Object.assign(new Error("Request aborted"), { name: "AbortError" })); return; }
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export function createNewsReceiptSender({ send, onStatus = () => {}, wait = waitForRetry }) {
  const observations = new Map();
  let stopped = false;
  function submit(receipt, { retry = false } = {}) {
    const sessionId = Number(receipt?.sessionId);
    const key = String(receipt?.key || "");
    const token = String(receipt?.token || "");
    if (stopped || !Number.isSafeInteger(sessionId) || sessionId <= 0 || !key || key.length > 128 || !token || token.length > 24000) {
      return Promise.resolve();
    }
    const identity = `${sessionId}:${key}`;
    const previous = observations.get(identity);
    if (previous && (!retry || previous.status !== "error")) return previous.promise;
    const controller = new AbortController();
    const entry = { status: "saving", controller, promise: null };
    observations.set(identity, entry);
    const announce = (status, error = "") => {
      entry.status = status;
      if (!stopped && !controller.signal.aborted) onStatus({ sessionId, key, status, error });
    };
    announce("saving");
    entry.promise = (async () => {
      for (let attempt = 0; attempt < 2; attempt += 1) {
        try {
          const result = await send(sessionId, token, { signal: controller.signal });
          if (!result?.ok) throw new Error("뉴스 수신 기록을 저장하지 못했어요.");
          announce("ready");
          return;
        } catch (error) {
          if (stopped || controller.signal.aborted) return;
          const terminal = error?.status >= 400 && error.status < 500 && error.status !== 429;
          if (attempt === 1 || terminal) {
            announce("error", "뉴스 수신 기록 저장에 실패했어요. 뉴스 표시는 계속됩니다.");
            return;
          }
          try { await wait(1200, controller.signal); } catch (_) { return; }
        }
      }
    })();
    // Keep browser memory bounded; durable idempotency belongs to the server.
    while (observations.size > 200) {
      const oldest = observations.keys().next().value;
      const old = observations.get(oldest);
      old.controller.abort();
      observations.delete(oldest);
    }
    return entry.promise;
  }
  return {
    submit,
    stop() { stopped = true; for (const entry of observations.values()) entry.controller.abort(); observations.clear(); },
  };
}
