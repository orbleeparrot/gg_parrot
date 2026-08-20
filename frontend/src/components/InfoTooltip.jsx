import { useEffect, useId, useRef, useState } from "react";
import { GLOSSARY } from "../lib/glossary.js";

// ⓘ help icon that reveals a plain-language explanation.
// Desktop: hover. Mobile/touch: tap toggles (and tap-outside closes).
// placement: "top" (default) or "bottom" — use "bottom" near the page top where
// an upward tooltip would be clipped (e.g. the kimchi banner).
export default function InfoTooltip({ term, text, placement = "top" }) {
  const [open, setOpen] = useState(false);
  const [shift, setShift] = useState(0); // px nudge to keep the bubble on screen
  const ref = useRef(null);
  const tipRef = useRef(null);
  const openBeforePointerRef = useRef(false);
  const tipId = useId();
  const content = text || GLOSSARY[term] || "";
  const posCls =
    placement === "bottom"
      ? "top-full mt-2"
      : "bottom-full mb-2";

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    const onKeyDown = (e) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("click", onDocClick);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("click", onDocClick);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // The bubble is centred on a 16px icon, so near either edge it would hang off
  // and give the whole page a horizontal scrollbar. Measure once per open and
  // slide it back inside.
  //
  // 기준은 뷰포트가 아니라 본문 영역(.site-frame)이다 — 데스크톱에서는 왼쪽
  // 232px 를 고정 사이드바가 차지하고 있어서, 뷰포트 기준으로 밀어 넣으면
  // 사이드바 밑으로 들어가 설명이 가려진다(포지션의 롱·숏 ⓘ 가 그랬다).
  useEffect(() => {
    if (!open) {
      setShift(0);
      return;
    }
    const el = tipRef.current;
    if (!el) return;
    const frame = ref.current?.closest(".site-frame")?.getBoundingClientRect();
    const margin = 8;
    const minLeft = Math.max(0, frame ? frame.left : 0) + margin;
    const maxRight = Math.min(window.innerWidth, frame ? frame.right : window.innerWidth) - margin;
    const r = el.getBoundingClientRect();
    if (r.left < minLeft) setShift(minLeft - r.left);
    else if (r.right > maxRight) setShift(maxRight - r.right);
  }, [open]);

  if (!content) return null;

  return (
    // 13px 캡션부터 17px 제목까지 어디에나 인라인으로 끼므로, 글자 크기를 따라가는
    // em 기준으로 내린다 — px 로 맞추면 큰 글자 옆에서 위로 떠 보인다.
    <span
      ref={ref}
      className="relative inline-flex items-center align-[-0.15em] ml-1"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      {/* 아이콘은 16px 로 두되 실제 히트박스는 ::after 로 32px 까지 넓힌다.
          44px 까지 키우면 문장 안 이웃 글자의 클릭을 가로챈다. */}
      <button
        type="button"
        aria-label="설명 보기"
        aria-expanded={open}
        aria-controls={open ? tipId : undefined}
        aria-describedby={open ? tipId : undefined}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onPointerDown={() => {
          openBeforePointerRef.current = open;
        }}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          // A touch click focuses before it fires. Toggle from the state at
          // pointer-down so the first tap opens instead of opening on focus and
          // immediately closing again.
          setOpen(e.detail === 0 ? (value) => !value : !openBeforePointerRef.current);
        }}
        className="info-trigger relative w-4 h-4 rounded-full bg-slate-200 text-[10px] leading-none text-slate-700 flex items-center justify-center hover:bg-slate-300 after:absolute after:-inset-2 after:content-['']"
      >
        ⓘ
      </button>
      {open && (
        // 툴팁은 실제로 무언가를 덮으므로 그림자를 허용한다(§7 떠 있는 것).
        // 바닥은 캔버스가 아니라 surface — 다크에서 캔버스면 페이지와 같은 색이 된다.
        <span
          ref={tipRef}
          id={tipId}
          role="tooltip"
          style={{ transform: `translateX(calc(-50% + ${shift}px))` }}
          className={
            "absolute left-1/2 w-56 max-w-[calc(100vw-1rem)] z-[75] " +
            posCls +
            " rounded-xl bg-surface border border-slate-300 px-3 py-3" +
            " t-caption leading-relaxed text-slate-700 shadow-xl"
          }
        >
          {content}
        </span>
      )}
    </span>
  );
}
