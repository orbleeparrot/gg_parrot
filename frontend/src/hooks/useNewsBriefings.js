import { useCallback, useEffect, useRef, useState } from "react";
import { createNewsBriefingQueue } from "../lib/newsBriefings.js";

export default function useNewsBriefings(keys, load, concurrency = 2) {
  const [states, setStates] = useState({});
  const queueRef = useRef(null);
  const loadRef = useRef(load);
  loadRef.current = load;
  const keysKey = JSON.stringify(keys);

  useEffect(() => {
    const requested = JSON.parse(keysKey);
    setStates(Object.fromEntries(requested.map((key) => [key, { status: "queued", data: null, error: "" }])));
    const queue = createNewsBriefingQueue({
      keys: requested,
      load: (key, signal) => loadRef.current(key, signal),
      concurrency,
      onChange: (key, state) => setStates((current) => ({ ...current, [key]: state })),
    });
    queueRef.current = queue;
    const syncVisibility = () => queue.setVisible(!document.hidden);
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    queue.start();
    return () => {
      document.removeEventListener("visibilitychange", syncVisibility);
      queue.stop();
      if (queueRef.current === queue) queueRef.current = null;
    };
  }, [keysKey, concurrency]);

  const retry = useCallback((key) => queueRef.current?.retry(key), []);
  return { states, retry };
}
