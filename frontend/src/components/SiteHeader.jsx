import { useEffect, useRef, useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api } from "../api.js";
import { clearAuth, getAuthUser, getToken, updateAuthUser, useAuth } from "../lib/auth.js";
import MarketContext from "./MarketContext.jsx";
import { BrandLink } from "./SiteNavigation.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { RunnerKeyPanel } from "./RunnerSessions.jsx";
import { ActivityIcon, ChevronDownIcon, ChevronRightIcon, CloseIcon, DownloadIcon, HelpIcon, KeyIcon, LogoutIcon, MonitorIcon, UserIcon } from "./utilityIcons.jsx";
import "./SiteHeader.css";

export default function SiteHeader({ hasSidebar, onOpenNavigation, menuButtonRef }) {
  const { pathname, search } = useLocation();
  const { token } = useAuth();
  const helpOpen = pathname === "/" && new URLSearchParams(search).has("help");
  const docsParams = new URLSearchParams(pathname === "/" ? search : "");
  docsParams.set("help", "start");
  ["guide", "tour", "resume"].forEach((key) => docsParams.delete(key));

  return (
    <header className="site-header glass">
      <div className="site-header-inner">
        <div className="site-header-leading">
          {hasSidebar ? (
            <button ref={menuButtonRef} type="button" onClick={onOpenNavigation} className="site-mobile-menu-button" aria-label="페이지 메뉴 열기" aria-controls="site-mobile-navigation">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
            </button>
          ) : null}
          <BrandLink className={hasSidebar ? "site-mobile-brand" : "site-auth-brand"} />
        </div>
        {hasSidebar ? <MarketContext /> : null}
        <div className="site-header-utility header-tools">
          {hasSidebar ? (
            <nav className="header-resources" aria-label="설치 및 사용 안내">
              <NavLink to="/runner/install" className="header-control header-install t-label" aria-label="실행기 설치">
                <DownloadIcon /><span className="header-resource-label">실행기 설치</span>
                <span className="header-tooltip" aria-hidden="true">실행기 설치</span>
              </NavLink>
              <Link to={`/?${docsParams.toString()}`} state={pathname === "/" ? undefined : { helpReturnTo: pathname + search }} className="header-control header-help t-label" aria-haspopup="dialog" aria-expanded={helpOpen} aria-current={helpOpen ? "page" : undefined} aria-label="사용법">
                <HelpIcon /><span className="header-resource-label">사용법</span>
                <span className="header-tooltip" aria-hidden="true">사용법</span>
              </Link>
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

function Avatar({ user, large = false }) {
  const initial = String(user?.username || "?").trim().charAt(0).toUpperCase() || "?";
  return <span className={`account-avatar${large ? " is-large" : ""}${user ? " is-member" : ""}`} aria-hidden="true">{user ? initial : <UserIcon />}</span>;
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
    api.me().then((data) => {
      if (!active || getToken() !== token) return;
      updateAuthUser(data.user);
      setPointsState("idle");
    }).catch((reason) => {
      if (!active || getToken() !== token) return;
      if (reason.status === 401) clearAuth();
      else setPointsState("error");
    });
    return () => { active = false; };
  }, [token]);

  if (!member && ["/login", "/forgot", "/reset"].includes(pathname)) return null;

  const next = encodeURIComponent(pathname + search);
  const points = user?.points_balance == null ? "—" : user.points_balance.toLocaleString();
  const close = () => {
    setOpen(false);
    triggerRef.current?.focus({ preventScroll: true });
  };

  return (
    <div className="account-menu-root" ref={rootRef}>
      <button ref={triggerRef} type="button" className={`header-control account-trigger t-label${member ? " is-member" : " is-guest"}`} onClick={() => setOpen((value) => !value)} aria-haspopup="dialog" aria-expanded={open} aria-controls="header-account-menu" aria-label={member ? `${user.username} · 내 계정` : "로그인 · 계정 메뉴"}>
        <Avatar user={member ? user : null} />
        <span className="account-trigger-label">{member ? user.username : "로그인"}</span>
        <span className="account-trigger-chevron"><ChevronDownIcon /></span>
      </button>
      {open ? (
        <section ref={panelRef} id="header-account-menu" className="account-popover" role="dialog" aria-labelledby="header-account-title" tabIndex={-1}>
          <div className="account-identity">
            <Avatar user={member ? user : null} large />
            <div className="account-identity-copy">
              {member ? (
                <>
                  <span className="t-caption">내 계정</span>
                  <h2 id="header-account-title" className="t-h4">{user.username}</h2>
                  {user.email ? <p className="t-small account-email">{user.email}</p> : null}
                </>
              ) : (
                <>
                  <h2 id="header-account-title" className="t-h4">내 매크로를 한곳에서</h2>
                  <p className="t-small">로그인하고 이어서 시작하세요.</p>
                </>
              )}
            </div>
            <button type="button" className="header-control account-close" onClick={close} aria-label="계정 메뉴 닫기"><CloseIcon /></button>
          </div>
          {member ? (
            <>
              <Link to="/mypage" className="account-balance" onClick={() => setOpen(false)} aria-label={`보유 포인트 ${points}P · 내역 보기`}>
                <span className="account-balance-copy">
                  <span className="t-small">보유 포인트</span>
                  <strong className="t-h2 num">{pointsState === "loading" ? "—" : points}<span className="t-label">P</span></strong>
                </span>
                <span className="account-balance-link t-small">내역 보기<ChevronRightIcon /></span>
              </Link>
              {pointsState !== "idle" ? <p className={`account-points-status t-small${pointsState === "error" ? " is-error" : ""}`} role="status">{pointsState === "loading" ? "포인트를 불러오는 중이에요." : user.points_balance == null ? "포인트를 불러오지 못했어요." : "마지막으로 확인한 포인트예요. 새로 불러오지 못했어요."}</p> : null}
              <nav className="account-links" aria-label="내 계정 기능">
                <Link to="/mypage" className="account-row" onClick={() => setOpen(false)}>
                  <ActivityIcon /><span><strong className="t-title">내 활동</strong><span className="t-small">만든 매크로와 구매 내역</span></span><ChevronRightIcon />
                </Link>
                <Link to="/agents" className="account-row" onClick={() => setOpen(false)}>
                  <MonitorIcon /><span><strong className="t-title">내 에이전트</strong><span className="t-small">실행 상태와 손익 확인</span></span><ChevronRightIcon />
                </Link>
                <button type="button" className="account-row" onClick={() => setKeyOpen((value) => !value)} aria-expanded={keyOpen} aria-controls="header-account-key">
                  <KeyIcon /><span><strong className="t-title">회원 키</strong><span className="t-small">내 PC 실행기에 계정 연결</span></span><ChevronDownIcon />
                </button>
                {keyOpen ? <div id="header-account-key" className="account-key"><RunnerKeyPanel key={token} /></div> : null}
              </nav>
              <div className="account-footer">
                <button type="button" className="account-logout t-label" onClick={() => { setOpen(false); clearAuth(); navigate("/"); }}><LogoutIcon />로그아웃</button>
              </div>
            </>
          ) : (
            <div className="account-guest-actions">
              <p className="t-small">저장한 매크로, 실행 현황, 포인트를<br />내 계정에서 확인할 수 있어요.</p>
              <Link to={`/login?next=${next}`} className="account-login t-label" onClick={() => setOpen(false)}>로그인<ChevronRightIcon /></Link>
              <div className="account-signup t-small"><span>아직 계정이 없나요?</span><Link to={`/login?mode=signup&next=${next}`} onClick={() => setOpen(false)}>회원가입<ChevronRightIcon /></Link></div>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
