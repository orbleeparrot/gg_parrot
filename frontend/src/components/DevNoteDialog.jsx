// 개발자 노트 — 들어오자마자 한 번 보여 주는 '이번 업데이트' 배너. "오늘 하루 보지 않기"를 누르면 자정(KST)까지 안 뜬다.
// 다크 팸플릿 배너(2026-10-02 사용자 선택): 테마와 상관없이 잉크 바탕 · 노랑 대형 활자 · 관절 껄무새 · 두 칸 타일.
// 이모지 대신 브랜드 일러스트·선 아이콘만 쓴다.
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { CURRENT_NOTE, dismissDevNote, shouldShowDevNote, todayKst } from "../lib/devNotes.js";
import { Icon } from "./icons.jsx";
import "./DevNoteDialog.css";

function storages() {
  try { return { local: window.localStorage, session: window.sessionStorage }; } catch { return { local: null, session: null }; }
}

export default function DevNoteDialog() {
  const [open, setOpen] = useState(false);
  const titleId = useId();
  const note = CURRENT_NOTE;

  useEffect(() => {
    const { local, session } = storages();
    setOpen(shouldShowDevNote({ local, session, todayKst: todayKst() }));
  }, []);

  const close = (hideToday = false) => {
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
  }, [open]);

  if (!open) return null;

  const action = (item) => (item.action
    ? <Link to={item.action.to} className="devnote-action" onClick={() => close()}>{item.action.label}<Icon name="arrowRight" size={14} /></Link>
    : null);
  const closeX = (
    <button type="button" className="devnote-x" aria-label="닫기" onClick={() => close()}><Icon name="x" size={20} /></button>
  );
  const later = (
    <button type="button" className="devnote-later" onClick={() => close(true)}>오늘 하루 보지 않기</button>
  );

  const body = (
    <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="devnote">
      {closeX}
      <div className="devnote-hero">
        <span className="devnote-chip">{note.eyebrow} · {note.date}</span>
        <h2 id={titleId}>껄무새가<br />이렇게<br />바뀌었어요</h2>
        <img className="devnote-hero-art" src="/brand/ggparrot-hero-articulated.svg" alt="" draggable="false" />
      </div>
      <ul className="devnote-items">
        {note.items.map((item) => (
          <li key={item.title}>
            <span className="devnote-item-icon" aria-hidden="true"><Icon name={item.icon} size={20} /></span>
            <span className="devnote-item-text">
              <b>{item.title}</b>
              <span>{item.text}</span>
              {action(item)}
            </span>
          </li>
        ))}
      </ul>
      <div className="devnote-foot">
        <button type="button" className="btn btn-l btn-primary w-full" onClick={() => close()}>확인했어요</button>
        {later}
      </div>
    </div>
  );

  return createPortal(
    <div className="scrim fixed inset-0 z-[95] grid place-items-center p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) close(); }}>
      {body}
    </div>,
    document.body,
  );
}
