import { useEffect, useRef, useState } from "react";

const STORAGE_KEY = "ggp_studio_condition_width";
const DEFAULT_WIDTH = 336;
const MIN_WIDTH = 280;
const MAX_WIDTH = 680;
const CHART_MIN_WIDTH = 480;
const clamp = (value, max) => Math.round(Math.min(max, Math.max(MIN_WIDTH, value)));

function savedWidth() {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(value) && value >= MIN_WIDTH) return clamp(value, MAX_WIDTH);
  } catch { /* 저장을 사용할 수 없어도 너비 조절은 동작한다. */ }
  return DEFAULT_WIDTH;
}

export default function useStudioSplit() {
  const workRef = useRef(null);
  const dragRef = useRef(null);
  const frameRef = useRef(null);
  const [preferredWidth, setPreferredWidth] = useState(savedWidth);
  const [availableWidth, setAvailableWidth] = useState(0);
  const [dragging, setDragging] = useState(false);
  const maxWidth = availableWidth
    ? Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, availableWidth - CHART_MIN_WIDTH - 1))
    : MAX_WIDTH;
  const width = clamp(preferredWidth, maxWidth);

  useEffect(() => {
    const element = workRef.current;
    const observer = new ResizeObserver(([entry]) => setAvailableWidth(entry.contentRect.width));
    observer.observe(element);
    return () => {
      observer.disconnect();
      cancelAnimationFrame(frameRef.current);
    };
  }, []);

  useEffect(() => {
    if (dragging) return;
    try { window.localStorage.setItem(STORAGE_KEY, String(preferredWidth)); } catch { /* 선택적 저장 */ }
  }, [preferredWidth, dragging]);

  function finishDrag(event) {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    setPreferredWidth(clamp(drag.nextWidth, maxWidth));
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return {
    workRef,
    width,
    dragging,
    separatorProps: {
      role: "separator",
      tabIndex: 0,
      "aria-label": "조건 패널 너비 조절",
      "aria-orientation": "vertical",
      "aria-controls": "studio-conditions",
      "aria-valuemin": MIN_WIDTH,
      "aria-valuemax": maxWidth,
      "aria-valuenow": width,
      "aria-valuetext": `${width}px`,
      title: "드래그하여 너비 조절 · 더블클릭으로 기본 너비",
      onPointerDown(event) {
        if (!event.isPrimary || event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        dragRef.current = { id: event.pointerId, startX: event.clientX, startWidth: width, nextWidth: width };
        setDragging(true);
      },
      onPointerMove(event) {
        const drag = dragRef.current;
        if (!drag || drag.id !== event.pointerId) return;
        drag.nextWidth = clamp(drag.startWidth + event.clientX - drag.startX, maxWidth);
        if (frameRef.current !== null) return;
        frameRef.current = requestAnimationFrame(() => {
          frameRef.current = null;
          if (dragRef.current) setPreferredWidth(dragRef.current.nextWidth);
        });
      },
      onPointerUp: finishDrag,
      onPointerCancel: finishDrag,
      onLostPointerCapture: finishDrag,
      onDoubleClick() { setPreferredWidth(clamp(DEFAULT_WIDTH, maxWidth)); },
      onKeyDown(event) {
        const step = event.shiftKey ? 48 : 16;
        const next = { ArrowLeft: width - step, ArrowRight: width + step, Home: MIN_WIDTH, End: maxWidth }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        setPreferredWidth(clamp(next, maxWidth));
      },
    },
  };
}
