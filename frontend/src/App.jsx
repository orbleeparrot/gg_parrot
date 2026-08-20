import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Home from "./pages/Home.jsx";
import { api } from "./api.js";
import { useAuth, clearAuth, getToken, updateAuthUser } from "./lib/auth.js";
// [차후 도입] 고래 동향 배너 — 거래소/컨트랙트 지갑 노이즈 정리 후 켤 예정.
// 컴포넌트와 백엔드(app/whales.py)는 그대로 두고 마운트만 꺼둡니다.
// import WhaleBanner from "./components/WhaleBanner.jsx";
import HotCoinsMarquee from "./components/HotCoinsMarquee.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import MarketContext from "./components/MarketContext.jsx";
import SiteNavigation, { BrandLink } from "./components/SiteNavigation.jsx";
import { RunnerKeyPanel } from "./components/RunnerSessions.jsx";

// Keep the first screen small and quick. The builder, charts, guide, and
// community screens are fetched only when their route is opened.
const Studio = lazy(() => import("./pages/Studio.jsx"));
const Leaderboard = lazy(() => import("./pages/Leaderboard.jsx"));
const Auth = lazy(() => import("./pages/Auth.jsx"));
const MyPage = lazy(() => import("./pages/MyPage.jsx"));
const Agents = lazy(() => import("./pages/Agents.jsx"));
const News = lazy(() => import("./pages/News.jsx"));
const Board = lazy(() => import("./pages/Board.jsx"));
const BoardPost = lazy(() => import("./pages/BoardPost.jsx"));
const ForgotPassword = lazy(() => import("./pages/ForgotPassword.jsx"));
const ResetPassword = lazy(() => import("./pages/ResetPassword.jsx"));

function useDismissibleMenu(open, setOpen, rootRef, triggerRef, routeKey) {
  useEffect(() => {
    setOpen(false);
  }, [routeKey, setOpen]);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event) => {
      if (!rootRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      setOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, rootRef, setOpen, triggerRef]);
}

function TopBar({ hasSidebar, onOpenNavigation, menuButtonRef }) {
  const { pathname, search } = useLocation();
  const helpOpen = pathname === "/" && new URLSearchParams(search).has("help");
  const docsParams = new URLSearchParams(pathname === "/" ? search : "");
  docsParams.set("help", "start");
  docsParams.delete("guide");
  docsParams.delete("tour");
  docsParams.delete("resume");
  const docsTo = `/?${docsParams.toString()}`;

  return (
    <header className="site-header glass">
      <div className="site-header-inner">
        <div className="site-header-leading">
          {hasSidebar ? (
            <button
              ref={menuButtonRef}
              type="button"
              onClick={onOpenNavigation}
              className="site-mobile-menu-button"
              aria-label="페이지 메뉴 열기"
              aria-controls="site-mobile-navigation"
            >
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" focusable="false"><path d="M4 7h16M4 12h16M4 17h16" /></svg>
            </button>
          ) : null}
          <BrandLink className={hasSidebar ? "site-mobile-brand" : "site-auth-brand"} />
        </div>
        {hasSidebar ? <MarketContext /> : null}
        <div className="site-header-utility">
          {hasSidebar ? (
            <Link
              to={docsTo}
              state={pathname === "/" ? undefined : { helpReturnTo: pathname + search }}
              className="site-help-link"
              aria-haspopup="dialog"
              aria-expanded={helpOpen}
              aria-current={helpOpen ? "page" : undefined}
            >
              사용 방법
            </Link>
          ) : null}
          {hasSidebar ? <RunnerKeyNav /> : null}
          <ThemeToggle />
          <AuthNav />
        </div>
      </div>
    </header>
  );
}

// 회원키(실행기 연동 키)를 상단바에서 바로 확인. 매크로 최초 등록 화면에서
// 키를 어디서 얻는지 몰라 막히는 것을 막기 위해 전역에서 접근 가능하게 둔다.
function RunnerKeyNav() {
  const { token } = useAuth();
  const { pathname, search } = useLocation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  useDismissibleMenu(open, setOpen, rootRef, triggerRef, pathname + search);

  if (!token) return null;

  return (
    <div ref={rootRef} className="site-menu-root site-runnerkey-root">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="site-help-link"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="site-runnerkey-panel"
      >
        회원키
      </button>
      {open ? (
        <div id="site-runnerkey-panel" className="site-runnerkey-panel" role="dialog" aria-label="껄무새 회원키">
          <RunnerKeyPanel enabled={open} />
        </div>
      ) : null}
    </div>
  );
}

function AuthNav() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  useDismissibleMenu(open, setOpen, rootRef, triggerRef, pathname + search);

  // Refresh the points balance from the server when logged in (keeps the header
  // in sync after unlocks/earnings made in other tabs).
  useEffect(() => {
    if (!token) return;
    let active = true;
    const requestedToken = token;
    api.me()
      .then((d) => {
        if (active && getToken() === requestedToken) updateAuthUser(d.user);
      })
      .catch((reason) => {
        if (active && getToken() === requestedToken && reason.status === 401) clearAuth();
      });
    return () => {
      active = false;
    };
  }, [token]);

  // 노란 채움은 화면당 하나(§1-4). 상단바는 어느 화면에나 얹히는 크롬이라
  // 여기서 노랑을 쓰면 본문의 주요 행동과 항상 겹친다 — 그래서 secondary 로 둔다.
  if (!token || !user) {
    if (["/login", "/forgot", "/reset"].includes(pathname)) {
      return null;
    }
    const next = encodeURIComponent(pathname + search);
    return (
      <div className="site-auth-actions">
        <button onClick={() => navigate(`/login?next=${next}`)} className="btn btn-s btn-ghost">
          로그인
        </button>
        <button onClick={() => navigate(`/login?mode=signup&next=${next}`)} className="btn btn-s btn-secondary site-signup-button">
          회원가입
        </button>
      </div>
    );
  }

  return (
    <div ref={rootRef} className="site-menu-root site-account-root">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="site-account-trigger"
        aria-expanded={open}
        aria-controls="site-account-menu"
        aria-label={`계정 메뉴 · ${user.username} · ${(user.points_balance ?? 0).toLocaleString()}포인트`}
      >
        <svg className="site-account-compact-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <circle cx="12" cy="8" r="3.25" />
          <path d="M5.75 19c.8-3.2 2.88-4.8 6.25-4.8s5.45 1.6 6.25 4.8" />
        </svg>
        <span className="site-account-name">{user.username}</span>
        <span className="num font-bold text-indigo-800">{(user.points_balance ?? 0).toLocaleString()}P</span>
        <svg className="site-account-chevron" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <path d={open ? "m4 10 4-4 4 4" : "m4 6 4 4 4-4"} />
        </svg>
      </button>
      {open ? (
        <div id="site-account-menu" className="site-account-panel">
          <button type="button" onClick={() => { setOpen(false); navigate("/mypage"); }}>
            <strong>내 활동</strong><span>등록한 매크로와 포인트</span>
          </button>
          <button type="button" onClick={() => { setOpen(false); navigate("/agents"); }}>
            <strong>내 에이전트</strong><span>매크로 실행과 상태 관리</span>
          </button>
          <button type="button" onClick={() => { setOpen(false); clearAuth(); navigate("/"); }}>
            <strong>로그아웃</strong><span>이 브라우저에서 계정 연결 끊기</span>
          </button>
        </div>
      ) : null}
    </div>
  );
}

function RouteChangeEffects() {
  const { pathname } = useLocation();
  const firstPath = useRef(pathname);

  useEffect(() => {
    const section = pathname === "/"
      ? "껄무새"
      : pathname.startsWith("/builder") || pathname.startsWith("/s/")
      ? "매크로 빌더"
      : pathname.startsWith("/leaderboard") || pathname.startsWith("/gallery")
      ? "리더보드"
      : pathname.startsWith("/news")
      ? "코인동향"
      : pathname.startsWith("/board")
      ? "게시판"
      : pathname.startsWith("/guide")
      ? "사용 가이드"
      : pathname.startsWith("/mypage")
      ? "내 활동"
      : pathname.startsWith("/agents")
      ? "내 에이전트"
      : pathname.startsWith("/runner")
      ? "빠른 실행"
      : pathname.startsWith("/login")
      ? "로그인"
      : "껄무새";
    document.title = section === "껄무새" ? section : `${section} · 껄무새`;
    if (firstPath.current === pathname) return;
    firstPath.current = pathname;
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    document.getElementById("main-content")?.focus({ preventScroll: true });
  }, [pathname]);

  return null;
}

function RouteLoading() {
  return (
    <div className="py-14 text-center t-small text-slate-500" role="status">
      화면 불러오는 중…
    </div>
  );
}

function LegacyStartRedirect({ view }) {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  if (view === "help") {
    params.set("help", "start");
    params.delete("step");
    params.delete("run");
  } else {
    params.delete("help");
    params.set("run", "1");
    if (!params.has("step")) params.set("step", "1");
  }
  params.delete("guide");
  params.delete("tour");
  params.delete("resume");
  const query = params.toString();
  return (
    <Navigate
      to={{ pathname: "/", search: query ? `?${query}` : "" }}
      replace
      state={location.state}
    />
  );
}

export default function App() {
  const { pathname } = useLocation();
  const isHome = pathname === "/";
  const isLegacyStart = pathname === "/runner" || pathname === "/guide";
  const isStart = isHome || isLegacyStart;
  const isNews = pathname === "/news";
  const isAgents = pathname === "/agents";
  const authShell = ["/login", "/forgot", "/reset"].includes(pathname);
  // '오늘의 경주마' 마퀴는 화면 아래에 고정으로 떠 있다. 띄우는 화면에서는 본문
  // 마지막 줄이 그 밑에 깔리므로, 마퀴 높이만큼 바닥 여백을 더 준다.
  const hasMarquee = !(isStart || authShell || isAgents);
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const menuButtonRef = useRef(null);
  const closeMobileNavigation = useCallback(() => setMobileNavigationOpen(false), []);

  useEffect(() => {
    setMobileNavigationOpen(false);
  }, [pathname]);

  return (
    <div className={`${isStart ? "home-shell" : "min-h-screen"} ${authShell ? "site-auth-layout" : "site-product-layout"}${hasMarquee ? " has-site-marquee" : ""}`}>
      <a href="#main-content" className="skip-link">본문으로 건너뛰기</a>
      {authShell ? null : (
        <SiteNavigation mobileOpen={mobileNavigationOpen} onClose={closeMobileNavigation} triggerRef={menuButtonRef} />
      )}
      <div
        className={authShell ? "site-frame is-auth" : "site-frame"}
        inert={mobileNavigationOpen ? "" : undefined}
        aria-hidden={mobileNavigationOpen || undefined}
      >
        <TopBar
          hasSidebar={!authShell}
          onOpenNavigation={() => setMobileNavigationOpen(true)}
          menuButtonRef={menuButtonRef}
        />
        {/* [차후 도입] <WhaleBanner /> */}
        <main
          id="main-content"
          tabIndex={-1}
          // 통합 시작 화면은 자체 전면 레이아웃이라 게터를 두지 않는다.
          // 나머지 본문 화면은 코인동향까지 포함해 전부 같은 게터(.site-main)를 쓴다.
          className={isStart
            ? "home-main"
            : authShell
              ? "site-main auth-main"
              : isAgents
                ? "site-main agent-main"
                : isNews
                ? "site-main news-main py-6 sm:py-8"
                : "site-main py-6 sm:py-8"}
        >
          <RouteChangeEffects />
          <Suspense fallback={<RouteLoading />}>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/builder" element={<Studio />} />
              <Route path="/s/:slug" element={<Studio />} />
              <Route path="/mypage" element={<MyPage />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/runner" element={<LegacyStartRedirect view="runner" />} />
              <Route path="/guide" element={<LegacyStartRedirect view="help" />} />
              <Route path="/news" element={<News />} />
              <Route path="/board" element={<Board />} />
              <Route path="/board/:id" element={<BoardPost />} />
              <Route path="/login" element={<Auth />} />
              <Route path="/forgot" element={<ForgotPassword />} />
              <Route path="/reset" element={<ResetPassword />} />
              <Route path="/leaderboard" element={<Leaderboard />} />
              <Route path="/gallery" element={<Navigate to="/leaderboard" replace />} />
              <Route path="*" element={<NotFound />} />
            </Routes>
          </Suspense>
        </main>
        {hasMarquee ? <HotCoinsMarquee /> : null}
      </div>
    </div>
  );
}

function NotFound() {
  const navigate = useNavigate();
  return (
    <div className="max-w-md mx-auto py-12 text-center">
      <div className="t-caption text-slate-500 num">404</div>
      <h1 className="mt-2 t-h2 text-slate-900">이 화면은 찾을 수 없어요</h1>
      <p className="mt-3 t-small text-slate-700">주소를 다시 확인하거나 시작 화면으로 돌아가요.</p>
      <button onClick={() => navigate("/")} className="mt-6 btn btn-l btn-primary">
        메인으로
      </button>
    </div>
  );
}
