// 개발자 노트 팝업 — 들어오자마자 한 번, 바뀐 것 다섯 줄. "오늘 하루만 보기"를 켜면 자정(KST)까지 안 뜬다.
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { CURRENT_NOTE, dismissDevNote, shouldShowDevNote, todayKst } from "../lib/devNotes.js";

function storages() {
  try { return { local: window.localStorage, session: window.sessionStorage }; } catch { return { local: null, session: null }; }
}

export default function DevNoteDialog() {
  const [open, setOpen] = useState(false);
  const [hideToday, setHideToday] = useState(false);
  const titleId = useId();

  useEffect(() => {
    const { local, session } = storages();
    setOpen(shouldShowDevNote({ local, session, todayKst: todayKst() }));
  }, []);

  const close = () => {
    const { local, session } = storages();
    dismissDevNote({ local, session, todayKst: todayKst(), hideToday });
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); close(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, hideToday]);

  if (!open) return null;
  const note = CURRENT_NOTE;

  return createPortal(
    <div className="scrim fixed inset-0 z-[95] grid place-items-center p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog devnote">
        <p className="t-caption text-slate-500">개발자 노트 · {note.id}</p>
        <h2 id={titleId} className="t-h4 text-slate-900 mt-1">{note.title}</h2>
        <ul className="devnote-list">
          {note.items.map((it) => (
            <li key={it.text} className="devnote-item">
              <span className="devnote-icon" aria-hidden="true">{it.icon}</span>
              <span className="t-small text-slate-800">
                {it.text}
                {it.action ? <> <Link to={it.action.to} className="devnote-link" onClick={close}>{it.action.label} →</Link></> : null}
              </span>
            </li>
          ))}
        </ul>
        <label className="devnote-check t-small text-slate-600">
          <input type="checkbox" checked={hideToday} onChange={(e) => setHideToday(e.target.checked)} />
          오늘 하루만 보기
        </label>
        <div className="confirm-dialog-actions">
          <button type="button" className="btn btn-l w-full btn-primary" onClick={close}>확인했어요</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
