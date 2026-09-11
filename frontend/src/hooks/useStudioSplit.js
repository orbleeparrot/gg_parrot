import { useEffect, useRef, useState } from "react";

const STORAGE_KEY = "ggp_studio_condition_width";
const COLLAPSED_KEY = "ggp_studio_conditions_collapsed";
const DEFAULT_WIDTH = 336;
const MIN_WIDTH = 280;
const MAX_WIDTH = 680;
const CHART_MIN_WIDTH = 480;
// 최소 너비를 맞추다가 조금 지나쳐도 접히지 않도록 충분한 드래그 여유를 둔다.
const COLLAPSE_WIDTH = MIN_WIDTH - 256;
const clamp = (value, max) => Math.round(Math.min(max, Math.max(MIN_WIDTH, value)));

function savedWidth() {
  try {
    const value = Number(window.localStorage.getItem(STORAGE_KEY));
    if (Number.isFinite(value) && value >= MIN_WIDTH) return clamp(value, MAX_WIDTH);
  } catch { /* 저장을 사용할 수 없어도 너비 조절은 동작한다. */ }
  return DEFAULT_WIDTH;
}

function savedCollapsed() {
  try { return window.localStorage.getItem(COLLAPSED_KEY) === "true"; } catch { return false; }
}

export default function useStudioSplit() {
  const workRef = useRef(null);
  const separatorRef = useRef(null);
  const panelRef = useRef(null);
  const reopenRef = useRef(null);
  const dragRef = useRef(null);
  const frameRef = useRef(null);
  const [preferredWidth, setPreferredWidth] = useState(savedWidth);
  const [collapsed, setCollapsed] = useState(savedCollapsed);
  const [desktop, setDesktop] = useState(() => window.matchMedia("(min-width: 1100px)").matches);
  const [availableWidth, setAvailableWidth] = useState(0);
  const maxWidth = availableWidth
    ? Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, availableWidth - CHART_MIN_WIDTH - 1))
    : MAX_WIDTH;
  const isCollapsed = desktop && collapsed;
  const width = isCollapsed ? 0 : clamp(preferredWidth, maxWidth);

  useEffect(() => {
    const query = window.matchMedia("(min-width: 1100px)");
    const onChange = () => setDesktop(query.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

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
  useEffect(() => {
    try { window.localStorage.setItem(COLLAPSED_KEY, String(collapsed)); } catch { /* 선택적 저장 */ }
  }, [collapsed]);

  // 매 프레임 React 화면 전체를 다시 그리지 않고 분할 너비만 바꾼다.
  // 폼/차트의 상태는 유지하고, 저장할 너비는 드래그가 끝난 뒤 한 번 반영한다.
  function paintWidth(nextWidth) {
    const closed = nextWidth === 0;
    workRef.current.style.setProperty("--studio-condition-width", `${nextWidth}px`);
    workRef.current.dataset.conditionsCollapsed = String(closed);
    panelRef.current.toggleAttribute("inert", closed);
    if (closed) panelRef.current.setAttribute("aria-hidden", "true");
    else panelRef.current.removeAttribute("aria-hidden");
    separatorRef.current.tabIndex = closed ? -1 : 0;
    separatorRef.current.setAttribute("aria-valuenow", String(nextWidth));
    separatorRef.current.setAttribute("aria-valuetext", closed ? "접힘" : `${nextWidth}px`);
  }

  function collapsePanel() {
    paintWidth(0);
    setCollapsed(true);
    reopenRef.current.focus({ preventScroll: true });
  }

  function reopenPanel() {
    paintWidth(clamp(preferredWidth, maxWidth));
    setCollapsed(false);
    separatorRef.current.focus({ preventScroll: true });
  }

  function finishDrag(event) {
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    if (event.type === "pointercancel") {
      paintWidth(width);
    } else if (drag.moved) {
      if (drag.nextWidth === 0) collapsePanel();
      else {
        const nextWidth = clamp(drag.nextWidth, maxWidth);
        paintWidth(nextWidth);
        setPreferredWidth(nextWidth);
        setCollapsed(false);
      }
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
    collapsed: isCollapsed,
    panelProps: { ref: panelRef, inert: isCollapsed ? "" : undefined, "aria-hidden": isCollapsed || undefined },
    reopenProps: {
      ref: reopenRef,
      "aria-label": "조건 펼치기",
      "aria-controls": "studio-conditions",
      "aria-expanded": false,
      title: "조건 펼치기",
      onClick: reopenPanel,
    },
    separatorProps: {
      ref: separatorRef,
      role: "separator",
      tabIndex: isCollapsed ? -1 : 0,
      "aria-label": "조건 패널 너비 조절",
      "aria-orientation": "vertical",
      "aria-controls": "studio-conditions",
      "aria-valuemin": 0,
      "aria-valuemax": maxWidth,
      "aria-valuenow": width,
      "aria-valuetext": isCollapsed ? "접힘" : `${width}px`,
      title: "드래그하여 너비 조절 · 왼쪽 끝으로 당겨 접기",
      onPointerDown(event) {
        if (!event.isPrimary || event.button !== 0 || isCollapsed) return;
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
        const rawWidth = drag.startWidth + event.clientX - drag.startX;
        // 접힘/펼침 경계를 다르게 두어 임계점 근처에서 흔들리지 않게 한다.
        const collapseAt = drag.nextWidth === 0 ? MIN_WIDTH : COLLAPSE_WIDTH;
        drag.nextWidth = rawWidth < collapseAt ? 0 : clamp(rawWidth, maxWidth);
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
        if (event.key === "ArrowLeft" && width === MIN_WIDTH) {
          event.preventDefault();
          collapsePanel();
          return;
        }
        const step = event.shiftKey ? 48 : 16;
        const next = { ArrowLeft: width - step, ArrowRight: width + step, Home: MIN_WIDTH, End: maxWidth }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        setPreferredWidth(clamp(next, maxWidth));
      },
    },
  };
}
