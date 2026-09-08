// Bound the whole response (including its body), while preserving caller cancellation.
export async function withRequestTimeout(task, { signal, timeoutMs } = {}) {
  if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) return task(signal);
  const controller = new AbortController();
  let timedOut = false;
  const cancel = () => controller.abort(signal.reason);
  if (signal?.aborted) cancel();
  else signal?.addEventListener("abort", cancel, { once: true });
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  try {
    return await task(controller.signal);
  } catch (reason) {
    if (timedOut && !signal?.aborted) {
      const error = new Error("응답이 지연되고 있어요. 잠시 후 다시 시도해 주세요.");
      error.name = "TimeoutError";
      throw error;
    }
    throw reason;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}
