// 개발자 노트 — 들어오자마자 한 번 보여 주는 '이번 업데이트' 배너. "오늘 하루 보지 않기"를 누르면 자정(KST)까지 안 뜬다.
// 다크 팸플릿 배너(2026-10-02 사용자 선택): 테마와 상관없이 잉크 바탕 · 노랑 대형 활자 · 껄무새(정지 SVG) · 두 칸 타일.
// 내용은 서버의 최신 노트(관리자 [공지] → AI 정리)를 쓰고, 없으면 lib/devNotes.js 의 기본 노트를 쓴다.
// 이모지 대신 브랜드 일러스트·선 아이콘만 쓴다.
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { CURRENT_NOTE, dismissDevNote, shouldShowDevNote, todayKst } from "../lib/devNotes.js";
import { Icon } from "./icons.jsx";
import "./DevNoteDialog.css";

function storages() {
  try { return { local: window.localStorage, session: window.sessionStorage }; } catch { return { local: null, session: null }; }
}

// 노트 한 장의 그림 — 첫 진입 배너와 관리자 미리보기가 같이 쓴다.
export function DevNoteBanner({ note, onClose, onHideToday }) {
  const titleId = useId();
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") { e.preventDefault(); onClose(); } };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  const headline = String(note.title || "껄무새가 이렇게 바뀌었어요");
  return createPortal(
    <div className="scrim fixed inset-0 z-95 grid place-items-center p-4" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} className="devnote">
        <button type="button" className="devnote-x" aria-label="닫기" onClick={onClose}><Icon name="x" size={20} /></button>
        <div className="devnote-hero">
          <span className="devnote-chip">{note.eyebrow || "이번 업데이트"} · {note.date}</span>
          <h2 id={titleId}>{headline}</h2>
          <img className="devnote-hero-art" src="/brand/ggparrot-sunglasses-hero-v2.svg" alt="" draggable="false" />
        </div>
        <ul className="devnote-items">
          {(note.items || []).map((item) => (
            <li key={item.title}>
              <span className="devnote-item-icon" aria-hidden="true"><Icon name={item.icon} size={20} /></span>
              <span className="devnote-item-text">
                <b>{item.title}</b>
                <span>{item.text}</span>
                {item.link ? (
                  <Link to={item.link} className="devnote-action" onClick={onClose}>{item.link_label || "보러 가기"}<Icon name="arrowRight" size={14} /></Link>
                ) : null}
              </span>
            </li>
          ))}
        </ul>
        <div className="devnote-foot">
          <button type="button" className="btn btn-l btn-primary w-full" onClick={onClose}>확인했어요</button>
          {onHideToday ? <button type="button" className="devnote-later" onClick={onHideToday}>오늘 하루 보지 않기</button> : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}

export default function DevNoteDialog() {
  const [note, setNote] = useState(null);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    const decide = (candidate) => {
      if (!alive) return;
      const { local, session } = storages();
      if (shouldShowDevNote({ local, session, todayKst: todayKst(), note: candidate })) setNote(candidate);
    };
    api.devnoteCurrent({ signal: controller.signal })
      .then((data) => decide(data?.note?.items?.length ? data.note : CURRENT_NOTE))
      .catch((reason) => { if (reason?.name !== "AbortError") decide(CURRENT_NOTE); });
    return () => { alive = false; controller.abort(); };
  }, []);

  if (!note) return null;
  const close = (hideToday = false) => {
    const { local, session } = storages();
    dismissDevNote({ local, session, todayKst: todayKst(), hideToday, note });
    setNote(null);
  };
  return <DevNoteBanner note={note} onClose={() => close()} onHideToday={() => close(true)} />;
}
