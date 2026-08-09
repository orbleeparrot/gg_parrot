import { useEffect, useRef } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

const PRIMARY_LINKS = [
  { to: "/", label: "홈", description: "서비스 소개", icon: "home", end: true },
  { to: "/runner", label: "빠른 실행", description: "준비부터 실행 확인까지", icon: "runner", matches: ["/runner"] },
  { to: "/builder", label: "직접 만들기", description: "조건 설계와 검증", icon: "builder", matches: ["/builder", "/s/"] },
  { to: "/leaderboard", label: "리더보드", description: "오늘의 모의 수익률", icon: "leaderboard", matches: ["/leaderboard", "/gallery"] },
];

const BROWSE_LINKS = [
  { to: "/news", label: "코인동향", description: "시장과 규제 뉴스", icon: "news", matches: ["/news"] },
  { to: "/board", label: "게시판", description: "질문과 전략 후기", icon: "board", matches: ["/board"] },
  { to: "/guide", label: "사용 가이드", description: "기능과 용어 읽기", icon: "guide", matches: ["/guide"] },
];

function ParrotMark() {
  return <img src="/brand/ggparrot-sal-mark.png" alt="" width="42" height="42" />;
}

export function BrandLink({ onClick, className = "" }) {
  return (
    <NavLink to="/" onClick={onClick} className={`site-brand text-slate-900 ${className}`} aria-label="껄무새 메인">
      <span className="site-brand-mark" aria-hidden="true"><ParrotMark /></span>
      <span className="brand-word">
        <strong>껄무새</strong>
        <small className="num">GGPARROT</small>
      </span>
    </NavLink>
  );
}

function NavGlyph({ name }) {
  const common = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.8, strokeLinecap: "round", strokeLinejoin: "round", focusable: "false" };
  if (name === "home") return <svg {...common}><path d="m3 10 9-7 9 7" /><path d="M5.5 9v11h13V9" /><path d="M9.5 20v-6h5v6" /></svg>;
  if (name === "builder") return <svg {...common}><path d="M4 5h16" /><path d="M4 12h16" /><path d="M4 19h16" /><circle cx="9" cy="5" r="2" fill="currentColor" stroke="none" /><circle cx="15" cy="12" r="2" fill="currentColor" stroke="none" /><circle cx="7" cy="19" r="2" fill="currentColor" stroke="none" /></svg>;
  if (name === "leaderboard") return <svg {...common}><path d="M5 20v-7h4v7" /><path d="M10 20V4h4v16" /><path d="M15 20v-11h4v11" /></svg>;
  if (name === "news") return <svg {...common}><path d="M4 5h16v14H4z" /><path d="M8 9h8" /><path d="M8 13h8" /><path d="M8 17h5" /></svg>;
  if (name === "board") return <svg {...common}><path d="M5 4h14v13H9l-4 3V4Z" /><path d="M8 8h8" /><path d="M8 12h6" /></svg>;
  if (name === "runner") return <svg {...common}><path d="M12 3v11" /><path d="m8 11 4 4 4-4" /><path d="M5 20h14" /></svg>;
  return <svg {...common}><path d="M5 4.5h9a3 3 0 0 1 3 3V20H8a3 3 0 0 1-3-3V4.5Z" /><path d="M8 8h6" /><path d="M8 12h6" /><path d="M17 8h2v12h-2" /></svg>;
}

function pathIsActive(pathname, link) {
  if (link.end) return pathname === link.to;
  return (link.matches || [link.to]).some((prefix) => pathname === prefix || pathname.startsWith(prefix));
}

function NavigationGroup({ label, links, pathname, tour, onNavigate, tabIndex }) {
  return (
    <div className="site-side-group">
      <div className="site-side-group-label">{label}</div>
      {links.map((link) => {
        const active = pathIsActive(pathname, link);
        const guideTarget = link.to === "/guide" && tour === "guide";
        return (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            onClick={onNavigate}
            tabIndex={tabIndex}
            className={`site-side-link ${active ? "is-active" : ""} ${guideTarget ? "is-guide-target" : ""}`}
          >
            <span className="site-side-icon" aria-hidden="true"><NavGlyph name={link.icon} /></span>
            <span><strong>{link.label}</strong><small>{link.description}</small></span>
          </NavLink>
        );
      })}
    </div>
  );
}

function NavigationContents({ onNavigate, tabIndex }) {
  const { pathname, search } = useLocation();
  const params = new URLSearchParams(search);
  const tour = pathname === "/" ? params.get("tour") || "" : "";

  return (
    <>
      <BrandLink onClick={onNavigate} className="site-sidebar-brand" />
      <nav className="site-side-nav" aria-label="전체 페이지">
        <NavigationGroup label="시작" links={PRIMARY_LINKS} pathname={pathname} tour={tour} onNavigate={onNavigate} tabIndex={tabIndex} />
        <NavigationGroup label="둘러보기" links={BROWSE_LINKS} pathname={pathname} tour={tour} onNavigate={onNavigate} tabIndex={tabIndex} />
      </nav>
      <div className="site-sidebar-bottom">
        <Link
          to="/runner"
          onClick={onNavigate}
          tabIndex={tabIndex}
          className="site-quick-start"
        >
          <span className="num">QUICK RUN</span>
          <strong>내 매크로 바로 실행</strong>
          <span aria-hidden="true">→</span>
        </Link>
        <p>모든 결과는 모의 계산이며<br />투자 조언이 아니에요.</p>
      </div>
    </>
  );
}

export default function SiteNavigation({ mobileOpen, onClose, triggerRef }) {
  const drawerRef = useRef(null);

  useEffect(() => {
    if (!mobileOpen) return undefined;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = window.requestAnimationFrame(() => drawerRef.current?.querySelector("a")?.focus());
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        window.requestAnimationFrame(() => triggerRef.current?.focus());
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(drawerRef.current?.querySelectorAll("a[href], button:not([disabled])") || []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [mobileOpen, onClose, triggerRef]);

  return (
    <>
      <aside className="site-sidebar" aria-label="페이지 사이드바">
        <NavigationContents />
      </aside>
      <button
        type="button"
        onClick={() => { onClose(); window.requestAnimationFrame(() => triggerRef.current?.focus()); }}
        className={`site-drawer-backdrop ${mobileOpen ? "is-open" : ""}`}
        aria-label="메뉴 닫기"
        tabIndex={mobileOpen ? 0 : -1}
      />
      <aside
        ref={drawerRef}
        id="site-mobile-navigation"
        className={`site-mobile-drawer ${mobileOpen ? "is-open" : ""}`}
        aria-label="모바일 페이지 메뉴"
        aria-hidden={!mobileOpen}
        inert={mobileOpen ? undefined : ""}
      >
        <button type="button" onClick={onClose} className="site-drawer-close" aria-label="메뉴 닫기">×</button>
        <NavigationContents onNavigate={onClose} tabIndex={mobileOpen ? undefined : -1} />
      </aside>
    </>
  );
}
