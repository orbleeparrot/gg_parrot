// Only brief gateway failures on reads may be replayed automatically.
// The caller owns the deadline, which covers attempts and both waits together.
const RETRY_DELAYS = [500, 1500];

function waitForRetry(delay, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason || new DOMException("The operation was aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", abort);
      resolve();
    }, delay);
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

export async function withGatewayRetry(task, { method, signal } = {}) {
  for (let attempt = 0; ; attempt += 1) {
    signal?.throwIfAborted();
    try {
      return await task();
    } catch (error) {
      const retryable = error?.status === 502 || error?.status === 504
        || (error?.status === 503 && error?.code === "TEMPORARY_SERVER_ERROR");
      if (method !== "GET" || !retryable || signal?.aborted || attempt >= RETRY_DELAYS.length) throw error;
      await waitForRetry(RETRY_DELAYS[attempt], signal);
    }
  }
}
