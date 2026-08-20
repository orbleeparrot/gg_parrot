import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// 가벼운 프로덕트 투어(온보딩). steps 의 각 항목이 data-tour="<anchor>" 요소를
// 스포트라이트로 비추고, 그 옆에 설명 카드를 띄운다. 대상이 화면에 없으면
// (예: 좁은 화면에서 숨겨진 상단 지표) 화면 중앙에 카드만 보여준다.
const HOLE_PADDING = 8;
const GAP = 12;
const EDGE = 12;

// 화면에 붙박이로 떠 있어 대상을 가릴 수 있는 것들. 상단바와 빌더의 고정 차트가
// 위를, '오늘의 경주마' 마퀴가 아래를 덮는다. 이걸 빼고 남는 띠 안에 대상을
// 넣어야 스포트라이트가 실제로 그 요소를 비춘다.
const STICKY_TOP_SELECTORS = [".site-header", ".builder-chart-sticky"];
const STICKY_BOTTOM_SELECTORS = [".site-marquee"];

// 상단에 실제로 '붙어 있는' 요소들의 아래 끝. sticky 는 고정되기 전에는 가리지
// 않으므로, 지금 자기 top 위치에 도달한 것만 센다.
function topObstruction(target) {
  let bottom = 0;
  for (const selector of STICKY_TOP_SELECTORS) {
    for (const el of document.querySelectorAll(selector)) {
      if (el === target || el.contains(target)) continue; // 대상 자신은 가리는 게 아니다
      const cs = window.getComputedStyle(el);
      if (cs.position !== "fixed" && cs.position !== "sticky") continue;
      const r = el.getBoundingClientRect();
      if (r.height === 0) continue;
      const pinnedAt = parseFloat(cs.top) || 0;
      if (r.top <= pinnedAt + 1 && r.bottom > bottom) bottom = r.bottom;
    }
  }
  return bottom;
}

function bottomObstruction() {
  let top = window.innerHeight;
  for (const selector of STICKY_BOTTOM_SELECTORS) {
    for (const el of document.querySelectorAll(selector)) {
      const r = el.getBoundingClientRect();
      if (r.height > 0 && r.top < top) top = r.top;
    }
  }
  return top;
}

// 대상을 '가려지지 않는 띠' 안으로 끌어온다. scrollIntoView({block:'center'}) 는
// 화면 정중앙에 두는데, 고정 차트가 상단 490px 를 덮고 있으면 정중앙이 곧 차트
// 밑이라 정작 설명하는 요소가 안 보인다.
//
// 두 번 도는 이유: 첫 스크롤로 sticky 가 비로소 고정되면서 가리는 높이가 달라진다.
function scrollIntoClearView(el) {
  for (let pass = 0; pass < 2; pass += 1) {
    const safeTop = topObstruction(el) + GAP + HOLE_PADDING;
    const safeBottom = bottomObstruction() - EDGE;
    const r = el.getBoundingClientRect();
    let delta = 0;
    if (r.top < safeTop) {
      delta = r.top - safeTop; // 음수 — 위로 스크롤해 대상을 내린다
    } else if (r.bottom > safeBottom) {
      // 아래로 넘침. 단 위가 가려질 만큼은 올리지 않는다(긴 요소는 윗부분 우선).
      delta = Math.max(0, Math.min(r.bottom - safeBottom, r.top - safeTop));
    }
    if (Math.abs(delta) < 1) return;
    window.scrollBy(0, delta);
  }
}

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

  // 투어 중에는 고정 차트를 풀어 화면을 통째로 쓴다. 419px 짜리가 상단에 붙어
  // 있으면 720px 화면에서 남는 띠가 200px 뿐이라, '거래 비용과 펀딩비' 처럼 긴
  // 섹션은 스크롤을 어떻게 맞춰도 다 들어가지 않는다. 투어는 폼을 훑는 동안이라
  // 차트를 붙잡아 둘 이유도 없다.
  useEffect(() => {
    if (!open) return undefined;
    const root = document.documentElement;
    root.classList.add("tour-active");
    return () => root.classList.remove("tour-active");
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
    if (el) {
      // 접힌 <details> 섹션(고급 위험 관리·거래 비용)은 내용이 보이도록 펼친다.
      if (el.tagName === "DETAILS" && !el.open) el.open = true;
      // 먼저 대략 화면 안으로 넣고, 붙박이 요소를 피해 자리를 잡는다.
      el.scrollIntoView({ block: "center", behavior: "auto" });
      scrollIntoClearView(el);
    }
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
    // 카드도 상단바·고정 차트·마퀴 밑으로 들어가면 안 된다.
    const anchorEl = step ? document.querySelector(`[data-tour="${step.anchor}"]`) : null;
    const limitTop = topObstruction(anchorEl) + EDGE;
    const limitBottom = bottomObstruction() - EDGE;
    if (!rect) {
      setCardPos({ top: Math.max(limitTop, (vh - ch) / 2), left: Math.max(EDGE, (vw - cw) / 2) });
      return;
    }
    const below = rect.top + rect.height + GAP;
    const above = rect.top - ch - GAP;
    let top;
    if (below + ch <= limitBottom) top = below;
    else if (above >= limitTop) top = above;
    else top = Math.max(limitTop, Math.min(limitBottom - ch, below));
    const left = Math.max(EDGE, Math.min(rect.left, vw - cw - EDGE));
    setCardPos({ top, left });
  }, [open, rect, index, step]);

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
