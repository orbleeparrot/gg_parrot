import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { clearAuth, getAuthUser, getToken, mergeFetchedAuthUser, updateAuthUser, useAuth } from "../lib/auth.js";
import MarketContext from "./MarketContext.jsx";
import { BrandLink } from "./SiteNavigation.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import UserAvatar from "./UserAvatar.jsx";
import { RunnerKeyPanel } from "./RunnerSessions.jsx";
import { ChevronDownIcon, DownloadIcon, HelpIcon, MenuIcon, UserIcon } from "./utilityIcons.jsx";
import "./SiteHeader.css";

export default function SiteHeader({ hasSidebar, onOpenNavigation, menuButtonRef }) {
  const { token } = useAuth();

  return (
    <header className="site-header glass">
      <div className="site-header-inner">
        <div className="site-header-leading">
          {hasSidebar ? (
            <button ref={menuButtonRef} type="button" onClick={onOpenNavigation} className="site-mobile-menu-button" aria-label="페이지 메뉴 열기" aria-controls="site-mobile-navigation">
              <MenuIcon />
            </button>
          ) : null}
          <BrandLink className={hasSidebar ? "site-mobile-brand" : "site-auth-brand"} />
        </div>
        {hasSidebar ? <MarketContext /> : null}
        <div className="site-header-utility header-tools">
          {hasSidebar ? (
            <nav className="header-resources" aria-label="설치 및 사용 안내">
              <NavLink to="/runner/install" className="header-control header-resource t-small" aria-label="실행기 설치">
                <DownloadIcon />
                <span className="header-resource-label">실행기 설치</span>
                <span className="header-tooltip" aria-hidden="true">실행기 설치</span>
              </NavLink>
              <NavLink to="/guide" className="header-control header-resource t-small" aria-label="사용법">
                <HelpIcon />
                <span className="header-resource-label">사용법</span>
                <span className="header-tooltip" aria-hidden="true">사용법</span>
              </NavLink>
            </nav>
          ) : null}
          <div className="header-personal">
            <ThemeToggle />
            {/* Remount on account changes so an open key never survives a switch. */}
            <AccountMenu key={token || "guest"} />
          </div>
        </div>
      </div>
    </header>
  );
}

function AccountMenu() {
  const { token, user } = useAuth();
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [pointsState, setPointsState] = useState(() => token && getAuthUser()?.points_balance == null ? "loading" : "idle");
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const panelRef = useRef(null);
  const member = Boolean(token && user);

  useEffect(() => { setOpen(false); }, [pathname, search]);
  useEffect(() => { if (!open) setKeyOpen(false); }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    panelRef.current?.focus({ preventScroll: true });
    const dismissOutside = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const onEscape = (event) => {
      if (event.key !== "Escape" || event.defaultPrevented) return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus({ preventScroll: true });
    };
    document.addEventListener("pointerdown", dismissOutside);
    document.addEventListener("focusin", dismissOutside);
    document.addEventListener("keydown", onEscape);
    return () => {
      document.removeEventListener("pointerdown", dismissOutside);
      document.removeEventListener("focusin", dismissOutside);
      document.removeEventListener("keydown", onEscape);
    };
  }, [open]);

  useEffect(() => {
    if (!token) return undefined;
    let active = true;
    const requestedUser = getAuthUser();
    api.me().then((data) => {
      if (!active || getToken() !== token) return;
      updateAuthUser(mergeFetchedAuthUser(data.user, requestedUser));
      setPointsState("idle");
    }).catch((reason) => {
      if (!active || getToken() !== token) return;
      if (reason.status === 401) clearAuth();
      else setPointsState("error");
    });
    return () => { active = false; };
  }, [token]);

  const next = encodeURIComponent(pathname + search);
  if (!member) {
    if (["/login", "/forgot", "/reset"].includes(pathname)) return null;
    return (
      <Link to={`/login?next=${next}`} className="header-control account-trigger" aria-label="로그인">
        <span className="header-avatar" aria-hidden="true"><UserIcon /></span>
        <span className="header-tooltip" aria-hidden="true">로그인</span>
      </Link>
    );
  }

  const points = user?.points_balance == null ? "—" : user.points_balance.toLocaleString();

  return (
    <div className="account-menu-root" ref={rootRef}>
      <button ref={triggerRef} type="button" className="header-control account-trigger" onClick={() => setOpen((value) => !value)} aria-haspopup="dialog" aria-expanded={open} aria-controls="header-account-menu" aria-label={`${user.username} · 계정 메뉴`}>
        <UserAvatar src={user.avatar_url} name={user.username} size={32} className="header-avatar" />
        <span className="header-tooltip" aria-hidden="true">{user.username}</span>
      </button>
      {open ? (
        <section ref={panelRef} id="header-account-menu" className="account-popover" role="dialog" aria-label="계정 메뉴" tabIndex={-1}>
          <div className="account-identity">
            <strong className="account-name t-label" title={user.username}>{user.username}</strong>
            <span className="account-points num t-small" aria-label={`보유 포인트 ${points}P`}>{points}<span>P</span></span>
          </div>
          {pointsState !== "idle" ? <p className={`account-points-status t-small${pointsState === "error" ? " is-error" : ""}`} role="status">{pointsState === "loading" ? "포인트 불러오는 중…" : user.points_balance == null ? "포인트 조회 실패" : "포인트 갱신 실패 · 이전 잔액"}</p> : null}
          <nav className="account-links" aria-label="내 계정 기능">
            <Link to="/mypage" className="account-row t-label" onClick={() => setOpen(false)}>내 활동</Link>
            <Link to="/agents" className="account-row t-label" onClick={() => setOpen(false)}>내 에이전트</Link>
            <button type="button" className="account-row t-label" onClick={() => setKeyOpen((value) => !value)} aria-expanded={keyOpen} aria-controls="header-account-key">회원 키<ChevronDownIcon /></button>
            {keyOpen ? <div id="header-account-key" className="account-key"><RunnerKeyPanel key={token} menu /></div> : null}
          </nav>
          <div className="account-footer">
            <button type="button" className="account-row t-label" onClick={() => { setOpen(false); clearAuth(); navigate("/"); }}>로그아웃</button>
          </div>
        </section>
      ) : null}
    </div>
  );
}
