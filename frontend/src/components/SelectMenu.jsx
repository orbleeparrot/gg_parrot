import { useEffect, useId, useRef, useState } from "react";

// 우리 모양의 드롭다운 — 브라우저 <select> 대신. 버튼(입력칸 모양) + 아래로 펼쳐지는 목록(dialog 표면).
// 키보드: Enter·Space·↓ 로 열고, ↑↓ 로 옮기고, Enter 로 고르고, Esc 로 닫는다. 바깥을 누르면 닫힌다.
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

  function onKeyDown(e) {
    if (!open) {
      if (["Enter", " ", "ArrowDown", "ArrowUp"].includes(e.key)) { e.preventDefault(); setOpen(true); }
      return;
    }
    if (e.key === "Escape") { e.preventDefault(); setOpen(false); return; }
    if (e.key === "ArrowDown") { e.preventDefault(); setActive((i) => Math.min(options.length - 1, i + 1)); return; }
    if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); return; }
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(active); return; }
    if (e.key === "Tab") setOpen(false);
  }

  return (
    <div ref={root} className={`select-menu${open ? " is-open" : ""} ${className}`} onKeyDown={onKeyDown}>
      <button type="button" className={`field field-${size} select-menu-trigger`} aria-haspopup="listbox" aria-expanded={open} aria-controls={listId} aria-label={label} onClick={() => setOpen((v) => !v)}>
        <span>{current?.[1]}</span>
        <span className="select-menu-caret" aria-hidden="true" />
      </button>
      {open ? (
        <ul id={listId} role="listbox" aria-label={label} className="select-menu-list">
          {options.map(([key, text], i) => (
            <li key={key} role="option" aria-selected={key === value} className={`select-menu-item${i === active ? " is-active" : ""}${key === value ? " is-selected" : ""}`} onMouseEnter={() => setActive(i)} onClick={() => pick(i)}>
              {text}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
