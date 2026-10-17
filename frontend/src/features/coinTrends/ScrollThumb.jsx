import { useEffect, useRef } from "react";
import { thumbGeometry } from "./trendsFormat.js";

// 안쪽 스크롤 칸의 막대(시안 B, 2026-10-17) — 브라우저 막대는 숨기고(CSS .ct-scroll), 칸에 마우스를 올리거나
// 스크롤하는 동안만 6px 막대를 보여 준다. 끌어서 움직일 수 있다. 칸 맨 앞 자식으로 둔다(sticky 로 칸 위에 붙음).
// 맨 위 · 맨 끝이 아니면 at-top · at-end 가 빠져 CSS 가 그쪽 끝을 흐린다(더 있다는 표시).
export default function ScrollThumb({ boxRef }) {
  const railRef = useRef(null);
  const thumbRef = useRef(null);
  useEffect(() => {
    const box = boxRef.current, rail = railRef.current, thumb = thumbRef.current;
    if (!box || !rail || !thumb) return undefined;
    let frame = 0, idle = 0;
    const measure = () => {
      frame = 0;
      const g = thumbGeometry(box);
      box.classList.toggle("at-top", g.atTop);
      box.classList.toggle("at-end", g.atEnd);
      rail.hidden = !g.scrollable;
      rail.style.height = `${g.track}px`;
      thumb.style.height = `${g.size}px`;
      thumb.style.transform = `translateY(${g.offset}px)`;
    };
    const schedule = () => { if (!frame) frame = requestAnimationFrame(measure); };
    const onScroll = () => {
      schedule();
      box.classList.add("is-scrolling");
      clearTimeout(idle);
      idle = setTimeout(() => box.classList.remove("is-scrolling"), 900);
    };
    // 목록이 늘거나(이어 받기) 칸 높이가 바뀌어도 막대 길이를 다시 잰다.
    const resize = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(schedule);
    const observeChildren = () => { if (resize) [...box.children].forEach((child) => resize.observe(child)); };
    resize?.observe(box);
    observeChildren();
    const mutation = typeof MutationObserver === "undefined" ? null : new MutationObserver(() => { observeChildren(); schedule(); });
    mutation?.observe(box, { childList: true });
    box.addEventListener("scroll", onScroll, { passive: true });

    let drag = null;
    const onDown = (event) => {
      event.preventDefault();
      thumb.setPointerCapture?.(event.pointerId);
      drag = { y: event.clientY, top: box.scrollTop, room: Math.max(1, box.clientHeight - 16 - thumb.offsetHeight) };
      box.classList.add("is-dragging");
    };
    const onMove = (event) => {
      if (!drag) return;
      box.scrollTop = drag.top + ((event.clientY - drag.y) / drag.room) * (box.scrollHeight - box.clientHeight);
    };
    const onUp = () => { drag = null; box.classList.remove("is-dragging"); };
    thumb.addEventListener("pointerdown", onDown);
    thumb.addEventListener("pointermove", onMove);
    thumb.addEventListener("pointerup", onUp);
    thumb.addEventListener("pointercancel", onUp);
    measure();
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(idle);
      resize?.disconnect();
      mutation?.disconnect();
      box.removeEventListener("scroll", onScroll);
      thumb.removeEventListener("pointerdown", onDown);
      thumb.removeEventListener("pointermove", onMove);
      thumb.removeEventListener("pointerup", onUp);
      thumb.removeEventListener("pointercancel", onUp);
    };
  }, [boxRef]);
  return (
    <div className="ct-thumb-anchor" aria-hidden="true">
      <div className="ct-thumb-rail" ref={railRef} hidden><i ref={thumbRef} /></div>
    </div>
  );
}
