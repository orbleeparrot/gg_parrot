export const MODEL = "semif-test:0.1.1";
export const VERDICTS = { bullish: "호재", bearish: "악재", neutral: "판단 유보" };
export const emptyTestState = () => ({ running: false, phase: "idle", current: null, history: [], count: 0, totalMs: 0, queueCount: 0, error: "" });
export function elapsedSeconds(ms) { return (Math.max(0, Number(ms) || 0) / 1000).toFixed(2); }

function wait(ms, signal) {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException("Stopped", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, ms);
    if (signal.aborted) abort();
    else signal.addEventListener("abort", abort, { once: true });
  });
}

// A single consumer: one article → one model call → publish immediately.
// Only server-returned inference time is stored; polling and display time are excluded.
export function createNewsTestRunner({ fetchFeed, analyze, onChange, now = Date.now, delay = wait, holdMs = 1500 }) {
  let state = emptyTestState();
  let controller = null;
  let cursor = "";
  let queue = [];
  const seen = new Set();
  const emit = (patch) => { state = { ...state, ...patch }; onChange(state); };
  const active = (c) => controller === c && !c.signal.aborted;
  async function start() {
    if (state.running) return;
    const c = new AbortController();
    controller = c;
    emit({ running: true, phase: "loading", error: "" });
    try {
      while (active(c)) {
        if (!queue.length) {
          const feed = await fetchFeed(cursor, { signal: c.signal });
          if (!active(c)) return;
          cursor = feed.cursor || cursor;
          for (const article of feed.items || []) {
            if (seen.has(article.id) || queue.some(item => item.id === article.id)) continue;
            queue.push(article);
          }
          emit({ queueCount: queue.length });
          if (!queue.length) {
            emit({ phase: "waiting" });
            await delay(feed.has_more ? 100 : 3000, c.signal);
            continue;
          }
        }
        const article = queue[0];
        const history = state.current?.result && state.current.article.id !== article.id
          ? [state.current, ...state.history].slice(0, 60) : state.history;
        emit({ phase: "analyzing", current: { article, startedAt: now(), result: null }, history, queueCount: queue.length - 1 });
        const result = await analyze({ id: article.id, scope: article.scope }, { signal: c.signal });
        if (!active(c)) return;
        if (!VERDICTS[result.verdict] || !Number.isFinite(result.elapsed_ms) || result.elapsed_ms < 0) throw new Error("판단 결과를 읽을 수 없어요.");
        queue.shift();
        seen.add(article.id);
        // A long-running test cannot retain an unbounded identity set.
        if (seen.size > 5000) seen.delete(seen.values().next().value);
        emit({ phase: "complete", current: { ...state.current, result }, count: state.count + 1,
          totalMs: state.totalMs + result.elapsed_ms, queueCount: queue.length });
        await delay(holdMs, c.signal);
      }
    } catch (error) {
      if (active(c)) emit({ running: false, phase: "error", error: error.message || "테스트를 진행하지 못했어요." });
    } finally {
      if (controller === c) controller = null;
    }
  }
  function stop() {
    const wasRunning = state.running;
    controller?.abort();
    controller = null;
    if (wasRunning) emit({ running: false, phase: "paused" });
  }
  return { start, stop, reset() { stop(); queue = []; cursor = ""; seen.clear(); state = emptyTestState(); onChange(state); } };
}
