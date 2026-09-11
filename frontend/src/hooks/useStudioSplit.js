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
  const separatorRef = useRef(null);
  const dragRef = useRef(null);
  const frameRef = useRef(null);
  const [preferredWidth, setPreferredWidth] = useState(savedWidth);
  const [availableWidth, setAvailableWidth] = useState(0);
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
    try { window.localStorage.setItem(STORAGE_KEY, String(preferredWidth)); } catch { /* 선택적 저장 */ }
  }, [preferredWidth]);

  // 매 프레임 React 화면 전체를 다시 그리지 않고 분할 너비만 바꾼다.
  // 폼/차트의 상태는 유지하고, 저장할 너비는 드래그가 끝난 뒤 한 번 반영한다.
  function paintWidth(nextWidth) {
    workRef.current.style.setProperty("--studio-condition-width", `${nextWidth}px`);
    separatorRef.current.setAttribute("aria-valuenow", String(nextWidth));
    separatorRef.current.setAttribute("aria-valuetext", `${nextWidth}px`);
  }

  function finishDrag(event) {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    if (drag.moved) {
      const nextWidth = clamp(drag.nextWidth, maxWidth);
      paintWidth(nextWidth);
      setPreferredWidth(nextWidth);
    }
    dragRef.current = null;
    workRef.current.classList.remove("is-resizing");
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return {
    workRef,
    width,
    separatorProps: {
      ref: separatorRef,
      role: "separator",
      tabIndex: 0,
      "aria-label": "조건 패널 너비 조절",
      "aria-orientation": "vertical",
      "aria-controls": "studio-conditions",
      "aria-valuemin": MIN_WIDTH,
      "aria-valuemax": maxWidth,
      "aria-valuenow": width,
      "aria-valuetext": `${width}px`,
      title: "드래그하여 너비 조절",
      onPointerDown(event) {
        if (!event.isPrimary || event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.focus({ preventScroll: true });
        event.currentTarget.setPointerCapture(event.pointerId);
        // 저장 값이 아니라 현재 화면의 경계선 위치에서 출발한다.
        const work = workRef.current.getBoundingClientRect();
        const actualWidth = event.currentTarget.getBoundingClientRect().left - work.left;
        dragRef.current = { id: event.pointerId, startX: event.clientX, startWidth: actualWidth, nextWidth: actualWidth, moved: false };
      },
      onPointerMove(event) {
        const drag = dragRef.current;
        if (!drag || drag.id !== event.pointerId) return;
        if (event.clientX === drag.startX && !drag.moved) return;
        drag.moved = true;
        drag.nextWidth = clamp(drag.startWidth + event.clientX - drag.startX, maxWidth);
        workRef.current.classList.add("is-resizing");
        if (frameRef.current !== null) return;
        frameRef.current = requestAnimationFrame(() => {
          frameRef.current = null;
          if (dragRef.current) paintWidth(dragRef.current.nextWidth);
        });
      },
      onPointerUp: finishDrag,
      onPointerCancel: finishDrag,
      onLostPointerCapture: finishDrag,
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
