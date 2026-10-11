import { useEffect, useId, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { macroSourceBadge } from "../lib/macroSource.js";
import CheckIcon from "./CheckIcon.jsx";
import { Icon } from "./icons.jsx";
// 드롭다운 틀(.studio-mode-menu · -item · -hint · -sep)을 빌더 종류 메뉴와 나눴다.
import "./BuilderModeMenu.css";

// 조건 판 머리의 매크로 출처 배지 + 드롭다운 (2026-09-23).
// 배지 하나가 '지금 조건이 어디서 왔는지'를 색·아이콘으로, '무슨 종목인지'를 티커 + 종목명으로 보여 준다.
// 누르면 '기본 빌더 ▾' 제목 메뉴와 같은 규격의 드롭다운 — 껄무새 후보 · 리더보드 · 직접 설정, 맨 아래 매크로 업로드.
// 색은 전부 index.css 의 --c-* 토큰(Studio.css .studio-src*). 로그인 전엔 껄무새·업로드 항목이 로그인 링크가 된다.
const ASK_MASCOT = "/brand/agent/ggparrot-agent-curious-v1.svg";

function SrcIcon({ kind }) {
  if (kind === "ask") return <img src={ASK_MASCOT} alt="" width="100" height="100" className="studio-src-face" aria-hidden="true" />;
  // 리더보드 = 트로피 · 업로드 = 업로드 화살표 · 시작 가이드 = 펼친 책 · 직접 설정 = 연필 (공용 Lucide 아이콘)
  const name = { board: "trophy", file: "upload", guide: "bookOpen", none: "pencilLine" }[kind];
  return name ? <Icon name={name} size={16} strokeWidth={2} className="studio-src-ic" /> : null;
}

// floating — 휴대폰 직접 만들기의 화면 아래 떠 있는 '껄무새에게 물어볼까?' 버튼(2026-10-08). 같은 메뉴가 위로 열린다.
export default function MacroSourceMenu({
  source, symbol, token, disabled = false, busy = false, flash = null, shared = false, floating = false,
  onAsk, onUpload, onReset, onBoard,
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const btnRef = useRef(null);
  const menuId = useId();
  const badge = macroSourceBadge(source, symbol);
  // 처음 들어와 아직 출처가 없으면 배지가 곧 '껄무새에게 물어볼까?' 입구다(공유 링크 화면 제외).
  const prompt = badge.kind === "none" && !shared;
  const tone = flash?.kind === "error" ? "error" : flash?.kind === "success" ? "success" : busy ? "loading" : prompt ? "prompt" : badge.tone;

  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => { if (!rootRef.current?.contains(e.target)) setOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") { setOpen(false); btnRef.current?.focus(); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);

  const pick = (fn) => () => { setOpen(false); fn?.(); };
  const displayTicker = badge.ticker || "—";
  const aria = busy ? "매크로 파일 읽는 중" : prompt ? "껄무새에게 물어볼까? · 매크로 출처 고르기" : `매크로 출처 · ${badge.title}`;

  // 배지 안 글자 — 로딩·오류·성공은 잠깐 다른 글자를 보여 준다(자세한 사유는 판 아래 알림 줄).
  let inner;
  if (tone === "loading") inner = <><span className="studio-src-spin" aria-hidden="true" /><span className="studio-src-nm">파일 읽는 중</span></>;
  else if (tone === "error") inner = <span className="studio-src-nm">{flash.text || "파일을 읽지 못했어요"}</span>;
  else if (tone === "success") inner = <><span className="studio-src-tk num">{displayTicker}</span><span className="studio-src-nm">{flash.text || "등록 완료"}</span></>;
  else if (tone === "prompt") inner = <><SrcIcon kind="ask" /><span className="studio-src-nm">껄무새에게 물어볼까?</span></>;
  else inner = (
    <>
      <SrcIcon kind={badge.kind} />
      <span className="studio-src-tk num">{displayTicker}</span>
      {badge.more ? <span className="studio-src-nm">+{badge.more}</span> : badge.name ? <span className="studio-src-nm">{badge.name}</span> : null}
    </>
  );

  return (
    <div className={"studio-src" + (floating ? " studio-fab" : "") + (prompt ? " is-prompt" : "")} ref={rootRef}>
      <button
        ref={btnRef}
        type="button"
        className={floating ? "studio-fab-btn" : `studio-src-badge is-${tone}`}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        aria-label={floating ? "껄무새에게 물어볼까? · 매크로 출처 고르기" : aria}
        title={floating ? undefined : badge.title}
      >
        {floating ? (
          <>
            <img src={ASK_MASCOT} alt="" width="100" height="100" className="studio-fab-face" aria-hidden="true" />
            <span>{busy ? "파일 읽는 중" : "껄무새에게 물어볼까?"}</span>
          </>
        ) : inner}
        <span className="studio-src-chev" aria-hidden="true" />
      </button>
      {open && (
        <div id={menuId} className="studio-mode-menu studio-src-menu" role="menu" aria-label="매크로 출처">
          {!shared && (token ? (
            <button type="button" role="menuitemradio" aria-checked={badge.kind === "ask"} className={"studio-mode-item" + (badge.kind === "ask" ? " is-on" : "")} onClick={pick(onAsk)}>
              <span className="studio-src-lead is-ask"><SrcIcon kind="ask" /></span>껄무새 후보
              {badge.kind === "ask" ? <CheckIcon className="studio-src-check" /> : null}
            </button>
          ) : (
            <Link to="/login?next=%2Fbuilder%3Fask%3D1" role="menuitem" className={"studio-mode-item" + (badge.kind === "ask" ? " is-on" : "")} onClick={() => setOpen(false)} title="물어보려면 로그인이 필요해요">
              <span className="studio-src-lead is-ask"><SrcIcon kind="ask" /></span>껄무새 후보{badge.kind === "ask" ? <CheckIcon className="studio-src-check" /> : <span className="studio-mode-hint">로그인</span>}
            </Link>
          ))}
          <button type="button" role="menuitemradio" aria-checked={badge.kind === "board"} className={"studio-mode-item" + (badge.kind === "board" ? " is-on" : "")} onClick={pick(onBoard)}>
            <span className="studio-src-lead is-board"><SrcIcon kind="board" /></span>리더보드에서 가져오기
            {badge.kind === "board" ? <CheckIcon className="studio-src-check" /> : null}
          </button>
          <button type="button" role="menuitemradio" aria-checked={badge.kind === "none"} className={"studio-mode-item" + (badge.kind === "none" ? " is-on" : "")} onClick={pick(onReset)}>
            <span className="studio-src-lead is-none"><SrcIcon kind="none" /></span>직접 설정
            {badge.kind !== "none" ? <span className="studio-mode-hint">해제</span> : <CheckIcon className="studio-src-check" />}
          </button>
          {!shared && (
            <>
              <hr className="studio-mode-sep" />
              {token ? (
                <button type="button" role="menuitem" className="studio-mode-item" onClick={pick(onUpload)} disabled={busy}>
                  <span className="studio-src-lead is-file"><SrcIcon kind="file" /></span>매크로 업로드<span className="studio-mode-hint">.ggm.json</span>
                </button>
              ) : (
                <Link to="/login?next=%2Fbuilder" role="menuitem" className="studio-mode-item" onClick={() => setOpen(false)} title="매크로 파일을 등록하려면 로그인이 필요해요">
                  <span className="studio-src-lead is-file"><SrcIcon kind="file" /></span>매크로 업로드<span className="studio-mode-hint">로그인</span>
                </Link>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
