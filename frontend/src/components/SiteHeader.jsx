import { useEffect } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { api } from "../api.js";
import { clearAuth, getAuthUser, getToken, mergeFetchedAuthUser, updateAuthUser, useAuth } from "../lib/auth.js";
import MarketContext from "./MarketContext.jsx";
import { BrandLink } from "./SiteNavigation.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import UserAvatar from "./UserAvatar.jsx";
import { DownloadIcon, HelpIcon, MenuIcon, UserIcon } from "./utilityIcons.jsx";
import "./SiteHeader.css";

export default function SiteHeader({ hasSidebar, onOpenNavigation, menuButtonRef, navigationOpen }) {
  const { token } = useAuth();

  return (
    <header className="site-header glass">
      <div className="site-header-inner">
        <div className="site-header-leading">
          {hasSidebar ? (
            <button ref={menuButtonRef} type="button" onClick={onOpenNavigation} className="site-mobile-menu-button" aria-label="페이지 메뉴 열기" aria-controls="site-mobile-navigation" aria-expanded={navigationOpen}>
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
            {/* Keep account hydration scoped to the current session. */}
            <ProfileLink key={token || "guest"} />
          </div>
        </div>
      </div>
    </header>
  );
}

function ProfileLink() {
  const { token, user } = useAuth();
  const { pathname, search } = useLocation();

  useEffect(() => {
    if (!token) return undefined;
    let active = true;
    const requestedUser = getAuthUser();
    api.me().then((data) => {
      if (!active || getToken() !== token) return;
      updateAuthUser(mergeFetchedAuthUser(data.user, requestedUser));
    }).catch((reason) => {
      if (active && getToken() === token && reason.status === 401) clearAuth();
    });
    return () => { active = false; };
  }, [token]);

  if (!token || !user) {
    if (["/login", "/forgot", "/reset"].includes(pathname)) return null;
    return <Link to={"/login?next=" + encodeURIComponent(pathname + search)} className="header-control account-trigger" aria-label="로그인">
      <span className="header-avatar" aria-hidden="true"><UserIcon /></span>
      <span className="header-tooltip" aria-hidden="true">로그인</span>
    </Link>;
  }

  return <Link to="/mypage" className="header-control account-trigger" aria-label="내 프로필" aria-current={pathname === "/mypage" ? "page" : undefined}>
    <UserAvatar src={user.avatar_url} name={user.username} size={32} className="header-avatar" />
    <span className="header-tooltip" aria-hidden="true">내 프로필</span>
  </Link>;
}
