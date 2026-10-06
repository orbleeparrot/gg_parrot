// 빌더 종류 전환 — 기본 빌더와 프로 빌더가 같은 메뉴를 쓴다.
// 전환은 양방향이어야 한다: 한쪽에만 두면 들어간 화면에서 나올 길이 없다.
// 어느 쪽으로 가든 지금 조건을 매크로로 싸서 들고 간다(onSwitch 가 라우팅한다).
import { useEffect, useRef, useState } from "react";
import { Icon } from "./icons.jsx";
import "./BuilderModeMenu.css";

export const BUILDER_MODES = Object.freeze([
  { value: "basic", label: "기본 빌더", path: "/builder" },
  { value: "pro", label: "프로 빌더", path: "/builder/pro" },
]);

export default function BuilderModeMenu({ mode = "basic", onSwitch, onTour = null, hint = "지금 조건 그대로" }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const onKey = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  const current = BUILDER_MODES.find((item) => item.value === mode) ?? BUILDER_MODES[0];
  return (
    <div className="studio-mode" ref={rootRef}>
      <button type="button" className="studio-mode-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        {current.label}<i className="studio-mode-chev" aria-hidden="true" />
      </button>
      {open && (
        <div className="studio-mode-menu" role="menu" aria-label="빌더 종류">
          {BUILDER_MODES.map((item) => {
            const on = item.value === current.value;
            return (
              <button
                key={item.value}
                type="button"
                role="menuitemradio"
                aria-checked={on}
                className={"studio-mode-item" + (on ? " is-on" : "")}
                onClick={() => { setOpen(false); if (!on) onSwitch?.(item); }}
              >
                <span className="studio-mode-check" aria-hidden="true">
                  {on ? <Icon name="check" size={14} strokeWidth={2.5} /> : null}
                </span>
                {item.label}
                {on ? null : <small className="studio-mode-hint">{hint}</small>}
              </button>
            );
          })}
          {onTour ? (
            <>
              <hr className="studio-mode-sep" aria-hidden="true" />
              {/* 항목별 설명 투어 — 화면 순서대로 각 칸을 비추며 설명한다. */}
              <button type="button" role="menuitem" className="studio-mode-item" onClick={() => { setOpen(false); onTour(); }}>
                <span className="studio-mode-check" aria-hidden="true"><Icon name="circleHelp" size={15} /></span>사용법 안내<small className="studio-mode-hint">화면 순서대로</small>
              </button>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
