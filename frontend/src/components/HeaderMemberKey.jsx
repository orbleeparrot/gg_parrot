// 헤더의 '회원 키' — 실행기 ④번 칸에 넣는 껄무새 회원 키를 어느 화면에서든 바로 꺼내 본다.
// 마이페이지 → 프로필 설정 → 보안까지 들어가야 보이던 걸 헤더 한 번 클릭으로 줄인다.
// 키 조회·복사·재발급은 기존 RunnerKeyPanel(menu)이 그대로 맡는다.
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useAuth } from "../lib/auth.js";
import { RunnerKeyPanel } from "./RunnerSessions.jsx";
import { KeyIcon } from "./utilityIcons.jsx";

export default function HeaderMemberKey() {
  const { token } = useAuth();
  const { pathname, search } = useLocation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);

  // 다른 페이지로 가면 닫고, 바깥 클릭·Esc 로도 닫는다.
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    if (!open) return undefined;
    const onPointer = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const onKey = (event) => {
      if (event.key === "Escape") { setOpen(false); buttonRef.current?.focus(); }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (!token) {
    return (
      <Link to={"/login?next=" + encodeURIComponent(pathname + search)} className="header-control header-resource t-small" aria-label="회원 키 (로그인 필요)">
        <KeyIcon />
        <span className="header-resource-label">회원 키</span>
        <span className="header-tooltip" aria-hidden="true">회원 키</span>
      </Link>
    );
  }

  return (
    <div ref={rootRef} className="header-key">
      <button
        ref={buttonRef}
        type="button"
        className="header-control header-resource t-small"
        aria-label="회원 키"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="header-member-key"
        onClick={() => setOpen((value) => !value)}
      >
        <KeyIcon />
        <span className="header-resource-label">회원 키</span>
        <span className="header-tooltip" aria-hidden="true">회원 키</span>
      </button>
      {open ? (
        <section id="header-member-key" className="header-key-popover" role="dialog" aria-label="껄무새 회원 키">
          <h2 className="t-title">껄무새 회원 키</h2>
          <p className="t-small text-slate-700">
            매크로 실행기의 <b className="text-slate-900">④ 회원 키</b> 칸에 넣어요. 계정당 1개이고, 거래소 API 키와는 다른 값이에요.
          </p>
          {/* 로그인 세션이 바뀌면 키도 새로 읽는다 */}
          <RunnerKeyPanel key={token} menu />
          <p className="t-caption text-slate-500">
            이 키로는 실행 상태 확인·원격 종료만 돼요. 거래소 키는 서버로 오지 않아요.
            {" "}<Link to="/runner/install" className="header-key-link">실행기 설치 안내</Link>
          </p>
        </section>
      ) : null}
    </div>
  );
}
