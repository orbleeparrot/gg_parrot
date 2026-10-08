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
      // database_busy — 서버 연결 풀이 몰림에 잠깐 찼다(GG-011, 2026-10-08). 서버도 Retry-After: 1 을 보낸다.
      // 예전엔 본문이 JSON 이라 재시도 대상에서 빠져 '서버 저장소가 혼잡해요'가 바로 화면에 떴다.
      const retryable = error?.status === 502 || error?.status === 504
        || (error?.status === 503 && (error?.code === "TEMPORARY_SERVER_ERROR" || error?.code === "database_busy"));
      if (method !== "GET" || !retryable || signal?.aborted || attempt >= RETRY_DELAYS.length) throw error;
      await waitForRetry(RETRY_DELAYS[attempt], signal);
    }
  }
}
