import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// 가벼운 프로덕트 투어(온보딩). steps 의 각 항목이 data-tour="<anchor>" 요소를
// 스포트라이트로 비추고, 그 옆에 설명 카드를 띄운다. 대상이 화면에 없으면
// (예: 좁은 화면에서 숨겨진 상단 지표) 화면 중앙에 카드만 보여준다.
const HOLE_PADDING = 8;
const GAP = 12;
const EDGE = 12;

export default function ProductTour({ steps, open, onClose }) {
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState(null);
  const [cardPos, setCardPos] = useState(null);
  const cardRef = useRef(null);

  const last = steps.length - 1;
  const step = open ? steps[index] : null;

  const next = useCallback(() => setIndex((i) => Math.min(last, i + 1)), [last]);
  const prev = useCallback(() => setIndex((i) => Math.max(0, i - 1)), []);

  // 열릴 때마다 첫 단계부터.
  useEffect(() => {
    if (open) setIndex(0);
  }, [open]);

  const measure = useCallback(() => {
    if (!step) return;
    const el = document.querySelector(`[data-tour="${step.anchor}"]`);
    if (!el) { setRect(null); return; }
    const r = el.getBoundingClientRect();
    // 숨겨진(0 크기) 대상은 없는 것으로 취급.
    if (r.width === 0 && r.height === 0) { setRect(null); return; }
    setRect({ top: r.top, left: r.left, width: r.width, height: r.height });
  }, [step]);

  // 단계가 바뀌면 대상을 화면 중앙으로 즉시 스크롤한 뒤 측정한다. 즉시 스크롤은
  // 동기적이라 바로 이어지는 측정이 최종 위치를 정확히 잡는다(부드러운 스크롤은
  // 빠른 단계 전환에서 타이밍이 어긋날 수 있어 쓰지 않는다).
  useLayoutEffect(() => {
    if (!step) return undefined;
    const el = document.querySelector(`[data-tour="${step.anchor}"]`);
    if (el) el.scrollIntoView({ block: "center", behavior: "auto" });
    measure();
    const timer = window.setTimeout(measure, 60);
    return () => window.clearTimeout(timer);
  }, [step, measure]);

  // 스크롤·리사이즈 중에도 스포트라이트가 대상을 따라가도록 재측정.
  useEffect(() => {
    if (!open) return undefined;
    const onMove = () => measure();
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open, measure]);

  // 카드 위치 계산: 대상 아래에 우선 배치, 공간이 없으면 위, 그래도 없으면 중앙.
  useLayoutEffect(() => {
    const card = cardRef.current;
    if (!open || !card) return;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const cw = card.offsetWidth;
    const ch = card.offsetHeight;
    if (!rect) {
      setCardPos({ top: Math.max(EDGE, (vh - ch) / 2), left: Math.max(EDGE, (vw - cw) / 2) });
      return;
    }
    const below = rect.top + rect.height + GAP;
    const above = rect.top - ch - GAP;
    let top;
    if (below + ch <= vh - EDGE) top = below;
    else if (above >= EDGE) top = above;
    else top = Math.max(EDGE, Math.min(vh - ch - EDGE, below));
    const left = Math.max(EDGE, Math.min(rect.left, vw - cw - EDGE));
    setCardPos({ top, left });
  }, [open, rect, index]);

  // 키보드: Esc 종료, ←/→ 이동.
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      else if (event.key === "ArrowRight") next();
      else if (event.key === "ArrowLeft") prev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, next, prev]);

  if (!open || !step) return null;

  const spot = rect
    ? {
        top: rect.top - HOLE_PADDING,
        left: rect.left - HOLE_PADDING,
        width: rect.width + HOLE_PADDING * 2,
        height: rect.height + HOLE_PADDING * 2,
      }
    : null;

  return createPortal(
    <div className="tour-root">
      {spot ? (
        <div className="tour-spotlight" style={spot} aria-hidden="true" />
      ) : (
        <div className="tour-dim" aria-hidden="true" />
      )}
      <div
        ref={cardRef}
        className="tour-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="tour-card-title"
        style={{
          top: cardPos?.top ?? 0,
          left: cardPos?.left ?? 0,
          visibility: cardPos ? "visible" : "hidden",
        }}
      >
        <div className="tour-card-top">
          <span className="tour-card-step num">{index + 1} / {steps.length}</span>
          <button type="button" className="tour-card-close" onClick={onClose} aria-label="사용법 안내 닫기">×</button>
        </div>
        <h3 id="tour-card-title" className="tour-card-title">{step.title}</h3>
        <p className="tour-card-body">{step.body}</p>
        <div className="tour-card-actions">
          <button type="button" className="tour-skip" onClick={onClose}>건너뛰기</button>
          <div className="tour-nav">
            {index > 0 ? (
              <button type="button" className="btn btn-s btn-secondary" onClick={prev}>이전</button>
            ) : null}
            {index < last ? (
              <button type="button" className="btn btn-s btn-primary" onClick={next}>다음</button>
            ) : (
              <button type="button" className="btn btn-s btn-primary" onClick={onClose}>완료</button>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}
