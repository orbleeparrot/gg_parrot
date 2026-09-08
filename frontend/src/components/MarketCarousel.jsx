// 시장·규제 캐러셀 — 기사마다 사진 한 장(og:image) 위에 제목을 얹은 카드.
//
// 가운데 카드만 선명하고 양옆은 유리 너머처럼 흐리다. 자동으로 넘어가지 않는다 —
// 화살표·키보드·스와이프·옆 카드 클릭으로만 움직인다. 사진이 없는 기사는
// 어두운 면에 출처 이름으로 대신한다.
import { useCallback, useEffect, useRef, useState } from "react";

const VISIBLE_EACH_SIDE = 2;
const SWIPE_THRESHOLD = 40;

function wrap(index, length) {
  return ((index % length) + length) % length;
}

// 가운데(0)에서 얼마나 떨어졌는지 — 순환이므로 가장 가까운 방향으로 센다.
function offsetFrom(active, index, length) {
  let delta = index - active;
  if (delta > length / 2) delta -= length;
  if (delta < -length / 2) delta += length;
  return delta;
}

export default function MarketCarousel({ items, ariaLabel = "시장·규제 헤드라인" }) {
  const [active, setActive] = useState(0);
  const pointerRef = useRef(null);
  const length = items.length;

  useEffect(() => {
    if (active >= length) setActive(0);
  }, [active, length]);

  const step = useCallback((delta) => {
    if (!length) return;
    setActive((current) => wrap(current + delta, length));
  }, [length]);

  const onKeyDown = (event) => {
    if (event.key === "ArrowLeft") { event.preventDefault(); step(-1); }
    if (event.key === "ArrowRight") { event.preventDefault(); step(1); }
  };
  const onPointerDown = (event) => {
    pointerRef.current = { x: event.clientX, id: event.pointerId };
  };
  const onPointerUp = (event) => {
    const start = pointerRef.current;
    pointerRef.current = null;
    if (!start || start.id !== event.pointerId) return;
    const dx = event.clientX - start.x;
    if (Math.abs(dx) >= SWIPE_THRESHOLD) step(dx < 0 ? 1 : -1);
  };

  if (!length) return null;
  const current = items[wrap(active, length)];

  return (
    <div className="news-carousel" aria-label={ariaLabel} role="group">
      <div
        className="news-carousel-stage"
        tabIndex={0}
        onKeyDown={onKeyDown}
        onPointerDown={onPointerDown}
        onPointerUp={onPointerUp}
        onPointerCancel={() => { pointerRef.current = null; }}
        aria-roledescription="carousel"
        aria-live="polite"
      >
        {items.map((item, index) => {
          const offset = offsetFrom(wrap(active, length), index, length);
          if (Math.abs(offset) > VISIBLE_EACH_SIDE) return null;
          const isActive = offset === 0;
          const Tag = isActive && item.url ? "a" : "button";
          const props = isActive && item.url
            ? { href: item.url, target: "_blank", rel: "noreferrer noopener" }
            : { type: "button", onClick: () => setActive(index), tabIndex: -1, "aria-label": `${item.title} — 가운데로 가져오기` };
          return (
            <Tag
              key={item.id || index}
              className={`news-carousel-card ${isActive ? "is-active" : ""} ${item.image ? "" : "is-plain"}`}
              style={{ "--offset": offset, ...(item.image ? { backgroundImage: `url("${item.image}")` } : null) }}
              aria-hidden={isActive ? undefined : true}
              {...props}
            >
              <span className="news-carousel-scrim" aria-hidden="true" />
              {!item.image ? <span className="news-carousel-plain-source" aria-hidden="true">{item.source}</span> : null}
              <span className="news-carousel-copy">
                <span className="news-carousel-title">{item.title}</span>
                <span className="news-carousel-meta">
                  <span className="news-carousel-source">{item.source}</span>
                  {item.time ? <span className="news-carousel-time">{item.time}</span> : null}
                  {isActive && item.url ? <span className="news-carousel-open">원문 열기 ↗</span> : null}
                </span>
              </span>
            </Tag>
          );
        })}
        <button type="button" className="news-carousel-arrow is-prev" onClick={() => step(-1)} aria-label="이전 기사">
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="M12.5 4 6.5 10l6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
        <button type="button" className="news-carousel-arrow is-next" onClick={() => step(1)} aria-label="다음 기사">
          <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true"><path d="m7.5 4 6 6-6 6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
        </button>
      </div>
      <div className="news-carousel-foot">
        <div className="news-carousel-dots" role="tablist" aria-label="기사 순서">
          {items.map((item, index) => (
            <button
              key={item.id || index}
              type="button"
              role="tab"
              aria-selected={index === wrap(active, length)}
              aria-label={`${index + 1}번째 기사`}
              className={`news-carousel-dot ${index === wrap(active, length) ? "is-on" : ""}`}
              onClick={() => setActive(index)}
            />
          ))}
        </div>
        <span className="news-carousel-count num" aria-live="polite">
          {wrap(active, length) + 1} <small>/ {length}</small>
        </span>
        <span className="sr-only">{current?.title}</span>
      </div>
    </div>
  );
}
