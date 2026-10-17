import { useCallback, useEffect, useRef, useState } from "react";

// 안쪽 스크롤 목록의 끝 표지 — 스크롤 상자 안에서 보이면 onReach 로 다음 묶음을 부른다.
// 불러오는 동안은 관찰을 쉬고, 끝나거나(실패 · 중복만 온 쪽 포함) count 가 바뀌면 관찰을 새로 건다 —
// 붙인 뒤에도 표지가 여전히 보이면(상자가 목록보다 길 때) 한 번 더 부른다.
export default function InfiniteSentinel({ rootRef, onReach, active, loading = false, count = 0, as: Tag = "div" }) {
  const ref = useRef(null);
  const reachRef = useRef(onReach);
  reachRef.current = onReach;
  useEffect(() => {
    const node = ref.current;
    if (!node || !active || loading || typeof IntersectionObserver === "undefined") return undefined;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) reachRef.current?.();
    }, { root: rootRef?.current || null, rootMargin: "0px 0px 160px 0px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [active, loading, rootRef, count]);
  if (!active) return null;
  return (
    <Tag ref={ref} className={loading ? "ct-loadmore is-loading" : "ct-loadmore"} aria-hidden="true">
      {loading ? "불러오는 중" : null}
    </Tag>
  );
}

// 이미 받아 둔 목록을 처음엔 일부만 그리고, 끝 표지가 보일 때마다 step 개씩 더 그린다.
export function useChunks(length, { first = 20, step = 20, resetKey = "" } = {}) {
  const [count, setCount] = useState(first);
  useEffect(() => { setCount(first); }, [resetKey, first]);
  const more = useCallback(() => setCount((current) => Math.min(length, current + step)), [length, step]);
  const shown = Math.min(count, length);
  return { shown, more, hasMore: shown < length };
}
