import { useEffect, useState, useSyncExternalStore } from "react";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";

export default function useBoardList(page, size, { sort = "new", q = "", field = "all" } = {}) {
  const { token } = useAuth();
  const version = useSyncExternalStore(api.subscribeBoardList, api.boardListVersion, api.boardListVersion);
  const [state, setState] = useState({ data: null, busy: true, err: "", now: Date.now() });
  useEffect(() => {
    if (!size) return undefined;
    const controller = new AbortController();
    let active = true;
    const cached = api.boardListCached(page, size, { sort, q, field });
    setState({ data: cached || null, busy: !cached, err: "", now: Date.now() });
    api.boardList(page, size, { sort, q, field, signal: controller.signal }).then(data => {
      if (active && version === api.boardListVersion()) setState({ data, busy: false, err: "", now: Date.now() });
    }).catch(error => {
      if (active && error.name !== "AbortError") setState(s => ({ ...s, busy: false, err: String(error.message || error) }));
    });
    return () => { active = false; controller.abort(); };
  }, [page, size, sort, q, field, token, version]);
  return state;
}
