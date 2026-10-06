import { useEffect, useId, useRef, useState } from "react";
import { useModalLayer } from "../hooks/useModalLayer.js";
import "./SelectMenu.css";

// 우리 모양의 드롭다운 — 브라우저 <select> 대신. 버튼(입력칸 모양) + 아래로 펼쳐지는 목록(dialog 표면).
// 키보드: Enter·Space·↓ 로 열고, ↑↓ 로 옮기고, Enter 로 고르고, Esc 로 닫는다. 바깥을 누르면 닫힌다.
// 화면 읽기 프로그램에는 '선택 전용 콤보상자'(WAI-ARIA APG)로 알린다 — 이름은 label, 값은 지금 고른 글자,
// 목록을 옮길 때는 aria-activedescendant 로 어느 항목인지. (예전엔 aria-label 이 값을 가려 지금 고른 범위를 들을 수 없었다.)
export default function SelectMenu({ value, options, onChange, label, className = "", size = "sm" }) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(() => Math.max(0, options.findIndex(([key]) => key === value)));
  const root = useRef(null);
  const listId = useId();
  const current = options.find(([key]) => key === value) || options[0];

  useEffect(() => {
    if (!open) return undefined;
    setActive(Math.max(0, options.findIndex(([key]) => key === value)));
    const onDown = (e) => { if (!root.current?.contains(e.target)) setOpen(false); };
    document.addEventListener("pointerdown", onDown);
    return () => document.removeEventListener("pointerdown", onDown);
  }, [open, options, value]);

  function pick(index) {
    const opt = options[index];
    if (opt) onChange(opt[0]);
    setOpen(false);
    root.current?.querySelector("button")?.focus();
  }

  // Esc 는 공용 겹으로 — 목록만 닫고 버튼에 포커스를 남긴다(모달 안에서 써도 모달까지 닫히지 않게).
  useModalLayer({ open, ref: root, onEscape: () => { setOpen(false); root.current?.querySelector("button")?.focus(); }, restoreFocus: false });

  function onKeyDown(e) {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); setOpen(true); }
      return;
    }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); return; }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(active); return; }
    if (e.key === "Tab") setOpen(false);
  }

  return (
    <div ref={root} className={`select-menu${open ? " is-open" : ""} ${className}`} onKeyDown={onKeyDown}>
      <button type="button" role="combobox" className={`field field-${size} select-menu-trigger`} aria-haspopup="listbox" aria-expanded={open} aria-controls={listId}
        aria-activedescendant={open ? `${listId}-${active}` : undefined} aria-label={label} onClick={() => setOpen((v) => !v)}>
        <span>{current?.[1]}</span>
        <span className="select-menu-caret" aria-hidden="true" />
      </button>
      {open ? (
        <ul id={listId} role="listbox" aria-label={label} className="select-menu-list">
          {options.map(([key, text], i) => (
            <li key={key} id={`${listId}-${i}`} role="option" aria-selected={key === value} className={`select-menu-item${i === active ? " is-active" : ""}${key === value ? " is-selected" : ""}`} onMouseEnter={() => setActive(i)} onClick={() => pick(i)}>
              {text}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
