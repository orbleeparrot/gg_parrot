import { useEffect, useLayoutEffect, useRef, useState } from "react";
import CommunityBodySummary from "./CommunityBodySummary.jsx";

const TICK_MS = 520;
const DEFAULT_VISIBLE_ROWS = 4;
const EMPTY_ITEMS = Object.freeze([]);

function mod(value, length) {
  return ((value % length) + length) % length;
}

function usePrefersReducedMotion() {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    const media = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(media.matches);
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, []);

  return reduced;
}

export default function NewsBriefingReader({
  items = EMPTY_ITEMS,
  ariaLabel,
  empty = "지금은 읽을 항목이 없어요.",
  queueLabel = "읽는 순서",
  rotateMs = 5_000,
  actionLabel = "원문 열기 ↗",
  onActivate,
  headingAs: Heading = "h3",
  queueOnly = false,
  rowHeight = 48,
  visibleRows = DEFAULT_VISIBLE_ROWS,
  syncTick,
}) {
  const reducedMotion = usePrefersReducedMotion();
  const [offset, setOffset] = useState(0);
  const [moving, setMoving] = useState(false);
  const [hoverPaused, setHoverPaused] = useState(false);
  const [focusPaused, setFocusPaused] = useState(false);
  const [pageHidden, setPageHidden] = useState(false);
  const [viewportPaused, setViewportPaused] = useState(false);
  const guardRef = useRef(null);
  const readerRef = useRef(null);
  const trackRef = useRef(null);
  const [measuredRows, setMeasuredRows] = useState(null);
  const previousSyncTickRef = useRef(syncTick);

  const total = items.length;
  const activeIndex = total ? mod(offset, total) : 0;
  const active = total ? items[activeIndex] : null;
  const rotates = total > 1;
  const visibleCount = Math.min(total, visibleRows);
  const hasCommunity = items.some((item) => item.community);
  const rows = total
    ? Array.from({ length: visibleCount + (rotates ? 1 : 0) }, (_, slot) => ({
        item: items[mod(offset + slot, total)],
        slot,
      }))
    : [];

  // Community summaries have natural height. Measure the visible rows and the
  // outgoing row so rotation never clips a longer summary or skips an article.
  useLayoutEffect(() => {
    if (!hasCommunity || !trackRef.current) return undefined;
    const nodes = [...trackRef.current.children];
    const measure = () => {
      const heights = nodes.map((node) => node.getBoundingClientRect().height);
      const next = { window: heights.slice(0, visibleCount).reduce((sum, height) => sum + height, 0),
        step: heights[0] || rowHeight };
      setMeasuredRows((previous) => previous?.window === next.window && previous?.step === next.step ? previous : next);
    };
    measure();
    if (!("ResizeObserver" in window)) return undefined;
    const observer = new ResizeObserver(measure);
    nodes.forEach((node) => observer.observe(node));
    return () => observer.disconnect();
  }, [hasCommunity, items, offset, rowHeight, visibleCount]);

  useEffect(() => {
    const sync = () => setPageHidden(document.hidden);
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);

  useEffect(() => {
    const node = readerRef.current;
    if (!node || !("IntersectionObserver" in window)) return undefined;
    const observer = new IntersectionObserver(
      ([entry]) => setViewportPaused(!entry.isIntersecting),
      { rootMargin: "120px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  function finishMove() {
    if (!moving) return;
    if (guardRef.current) window.clearTimeout(guardRef.current);
    guardRef.current = null;
    setOffset((value) => value + 1);
    setMoving(false);
  }

  useEffect(() => {
    if (!moving) return undefined;
    guardRef.current = window.setTimeout(finishMove, TICK_MS + 240);
    return () => {
      if (guardRef.current) window.clearTimeout(guardRef.current);
      guardRef.current = null;
    };
  }, [moving]);

  useEffect(() => {
    if (
      syncTick != null ||
      reducedMotion ||
      hoverPaused ||
      focusPaused ||
      pageHidden ||
      viewportPaused ||
      moving ||
      !rotates
    ) return undefined;

    const timer = window.setTimeout(() => setMoving(true), rotateMs);
    return () => window.clearTimeout(timer);
  }, [focusPaused, hoverPaused, moving, offset, pageHidden, reducedMotion, rotateMs, rotates, syncTick, viewportPaused]);

  useEffect(() => {
    if (syncTick == null) return;
    if (previousSyncTickRef.current === syncTick) return;
    previousSyncTickRef.current = syncTick;
    if (
      reducedMotion ||
      hoverPaused ||
      focusPaused ||
      pageHidden ||
      viewportPaused ||
      moving ||
      !rotates
    ) return;
    setMoving(true);
  }, [focusPaused, hoverPaused, moving, pageHidden, reducedMotion, rotates, syncTick, viewportPaused]);

  function activate(item, slot = 0) {
    if (slot !== 0) setOffset((value) => value + slot);
    setMoving(false);
    onActivate?.(item);
  }

  if (!active) {
    return <div className="news-reader-empty t-small text-slate-500">{empty}</div>;
  }

  return (
    <div
      ref={readerRef}
      className={`news-reader ${queueOnly ? "is-queue-only" : ""}`}
      style={{ "--news-reader-row-height": `${rowHeight}px` }}
      onMouseEnter={() => setHoverPaused(true)}
      onMouseLeave={() => setHoverPaused(false)}
      onFocusCapture={() => setFocusPaused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setFocusPaused(false);
      }}
    >
      {queueOnly ? null : (
        <article className="news-reader-lead" aria-label={ariaLabel}>
          <div key={active.id} className={`news-reader-current ${moving ? "is-exiting" : "is-entering"}`}>
            <div className="news-reader-utility-row">
              <p className="news-reader-meta">
                <span className="news-reader-status-dot" aria-hidden="true" />
                <strong>{active.source || "시장 데이터"}</strong>
                {active.time ? <span>{active.time}</span> : null}
              </p>
              {active.url ? (
                <a href={active.url} target="_blank" rel="noopener noreferrer">{actionLabel}</a>
              ) : onActivate ? (
                <button type="button" onClick={() => activate(active)}>{actionLabel}</button>
              ) : null}
            </div>
            <div className={`news-reader-title-row ${active.value ? "has-value" : ""}`}>
              <Heading>{active.title}</Heading>
              {active.value ? <strong className={`news-reader-value num ${active.tone || ""}`}>{active.value}</strong> : null}
            </div>
            {active.description ? <p className="news-reader-description">{active.description}</p> : null}
            <CommunityBodySummary summary={active.community?.summary} />
          </div>
        </article>
      )}

      <section className="news-reader-queue" aria-label={queueOnly ? ariaLabel || queueLabel : queueLabel}>
        {queueOnly ? null : (
          <header>
            <strong>{queueLabel}</strong>
            <div className="news-reader-queue-status">
              <span className="num">{activeIndex + 1} / {total}</span>
            </div>
          </header>
        )}
        <span className="sr-only">전체 {total}개 중 {activeIndex + 1}번째 뉴스</span>
        <div className="news-reader-queue-window" style={{ height: `${hasCommunity && measuredRows ? measuredRows.window : visibleCount * rowHeight}px` }}>
          <div
            ref={trackRef}
            className="news-reader-queue-track"
            style={{
              transform: `translateY(${moving ? -(hasCommunity && measuredRows ? measuredRows.step : rowHeight) : 0}px)`,
              transition: moving ? `transform ${TICK_MS}ms cubic-bezier(0.22, 0.61, 0.36, 1)` : "none",
            }}
            onTransitionEnd={(event) => {
              if (event.target === event.currentTarget && event.propertyName === "transform") finishMove();
            }}
          >
            {rows.map(({ item, slot }) => {
              const content = (
                item.community ? (
                  <>
                    <span className="news-reader-row-title">{item.title}</span>
                    <span className="news-reader-row-source news-reader-row-community-meta">
                      <span title={item.community.author || "작성자 미상"}>커뮤니티 · {item.community.author || "작성자 미상"}</span>
                      <span title={`${item.community.source || "Binance Square"} · ${item.time || "게시일 확인 불가"}`}>
                        {item.community.source || "Binance Square"} · {item.time || "게시일 확인 불가"}
                      </span>
                    </span>
                    <CommunityBodySummary summary={item.community.summary} />
                    <span className="news-reader-row-arrow" aria-hidden="true">{item.url ? "↗" : "→"}</span>
                  </>
                ) : queueOnly ? (
                  <>
                    <span className="news-reader-row-title">{item.title}</span>
                    <span className="news-reader-row-source">{item.rowLabel || item.source || "출처 미상"}</span>
                    <span className="news-reader-row-arrow" aria-hidden="true">{item.url ? "↗" : "→"}</span>
                  </>
                ) : (
                  <>
                    <span className="news-reader-row-source">{item.rowLabel || item.source || String(slot + 1).padStart(2, "0")}</span>
                    <span className="news-reader-row-title">{item.title}</span>
                    {item.rowValue ? <strong className={`news-reader-row-value num ${item.tone || ""}`}>{item.rowValue}</strong> : null}
                    {item.time ? <span className="news-reader-row-time">{item.time}</span> : null}
                    <span className="news-reader-row-arrow" aria-hidden="true">{item.url ? "↗" : "→"}</span>
                  </>
                )
              );
              const className = `news-reader-row ${slot === 0 ? "is-active" : ""} ${item.community ? "is-community" : ""}`;
              return item.url ? (
                <a key={`${item.id}:${slot}`} href={item.url} target="_blank" rel="noopener noreferrer" className={className} aria-current={slot === 0 ? "true" : undefined}>{content}</a>
              ) : (
                <button key={`${item.id}:${slot}`} type="button" onClick={() => activate(item, slot)} className={className} aria-current={slot === 0 ? "true" : undefined}>{content}</button>
              );
            })}
          </div>
        </div>
      </section>
    </div>
  );
}
