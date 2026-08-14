import { useEffect, useRef } from "react";
import { NavLink, useLocation } from "react-router-dom";

const NAV_LINKS = [
  { to: "/", label: "홈", icon: "home", end: true },
  { to: "/agents", label: "내 에이전트", icon: "agent", matches: ["/agents"] },
  { to: "/builder", label: "직접 만들기", icon: "builder", matches: ["/builder", "/s/"] },
  { to: "/leaderboard", label: "리더보드", icon: "leaderboard", matches: ["/leaderboard", "/gallery"] },
  { to: "/news", label: "코인동향", icon: "news", matches: ["/news"] },
  { to: "/board", label: "게시판", icon: "board", matches: ["/board"] },
];

const NAV_ICON_SOURCES = {
  home: "/brand/navigation/ggparrot-nav-home.png",
  agent: "/brand/navigation/ggparrot-nav-agent.png",
  builder: "/brand/navigation/ggparrot-nav-builder.png",
  leaderboard: "/brand/navigation/ggparrot-nav-leaderboard.png",
  news: "/brand/navigation/ggparrot-nav-news.png",
  board: "/brand/navigation/ggparrot-nav-board.png",
};

function ParrotMark() {
  return <img src="/brand/ggparrot-sunglasses-mark.png" alt="" width="42" height="42" draggable="false" />;
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

function NavIcon({ name }) {
  const src = NAV_ICON_SOURCES[name] || NAV_ICON_SOURCES.home;
  return (
    <img
      src={src}
      alt=""
      width="36"
      height="36"
      draggable="false"
      decoding="async"
    />
  );
}

function pathIsActive(pathname, link) {
  if (link.end) return pathname === link.to;
  return (link.matches || [link.to]).some((prefix) => pathname === prefix || pathname.startsWith(prefix));
}

function NavigationList({ pathname, onNavigate, tabIndex }) {
  return (
    <div className="site-side-list">
      {NAV_LINKS.map((link) => {
        const active = pathIsActive(pathname, link);
        return (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            onClick={onNavigate}
            tabIndex={tabIndex}
            aria-current={active ? "page" : undefined}
            className={`site-side-link ${active ? "is-active" : ""}`}
          >
            <span className="site-side-icon" aria-hidden="true"><NavIcon name={link.icon} /></span>
            <span className="site-side-label">{link.label}</span>
          </NavLink>
        );
      })}
    </div>
  );
}

function NavigationContents({ onNavigate, tabIndex }) {
  const { pathname } = useLocation();

  return (
    <>
      <BrandLink onClick={onNavigate} className="site-sidebar-brand" />
      <nav className="site-side-nav" aria-label="전체 페이지">
        <NavigationList pathname={pathname} onNavigate={onNavigate} tabIndex={tabIndex} />
      </nav>
      <div className="site-sidebar-bottom">
        <p>웹 결과는 모의 계산이며<br />투자 조언이 아니에요.</p>
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
