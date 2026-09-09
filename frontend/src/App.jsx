import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Home from "./pages/Home.jsx";
import { api } from "./api.js";
import { useAuth, clearAuth, getAuthUser, getToken, updateAuthUser } from "./lib/auth.js";
// [차후 도입] 고래 동향 배너 — 거래소/컨트랙트 지갑 노이즈 정리 후 켤 예정.
// 컴포넌트와 백엔드(app/whales.py)는 그대로 두고 마운트만 꺼둡니다.
// import WhaleBanner from "./components/WhaleBanner.jsx";
import HotCoinsMarquee from "./components/HotCoinsMarquee.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import MarketContext from "./components/MarketContext.jsx";
import SiteNavigation, { BrandLink } from "./components/SiteNavigation.jsx";
import { RunnerKeyPanel } from "./components/RunnerSessions.jsx";
import { DownloadIcon, HelpIcon, KeyIcon, UserIcon } from "./components/utilityIcons.jsx";

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
const RunnerInstall = lazy(() => import("./pages/RunnerInstall.jsx"));

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
        {/* 오른쪽 묶음 — 유틸리티 버튼 한 종류(.site-ub). 실행기 설치만 글자가 있는 알약(첫 방문자의 첫 행동),
            도움말·테마는 아이콘, 계정은 아바타(+포인트). 회원키는 계정 메뉴 안으로 — 실행기 연결 때만 쓰는 값이다. */}
        <div className="site-header-utility">
          {hasSidebar ? (
            <NavLink to="/runner/install" className="site-ub site-ub-cta" aria-label="실행기 설치">
              <DownloadIcon />
              <span className="site-ub-label">실행기 설치</span>
            </NavLink>
          ) : null}
          {hasSidebar ? (
            <Link
              to={docsTo}
              state={pathname === "/" ? undefined : { helpReturnTo: pathname + search }}
              className="site-ub site-ub-icon"
              aria-haspopup="dialog"
              aria-expanded={helpOpen}
              aria-current={helpOpen ? "page" : undefined}
              aria-label="사용 방법"
            >
              <HelpIcon />
              <span className="site-ub-tip" aria-hidden="true">사용 방법</span>
            </Link>
          ) : null}
          <ThemeToggle />
          <AuthNav />
        </div>
      </div>
    </header>
  );
}

// 계정 — 로그인 전엔 사람 아이콘, 로그인하면 이름 첫 글자 아바타 + 포인트. 누르면 메뉴가 열린다.
// 상단바는 어느 화면에나 얹히는 크롬이라 노랑을 쓰지 않는다(§1-4) — 메뉴 안의 로그인도 secondary.
function AuthNav() {
  const { token, user } = useAuth();
  const navigate = useNavigate();
  const { pathname, search } = useLocation();
  const [open, setOpen] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  // 아바타 버튼의 상태: idle · loading(잔고를 처음 받는 중) · error(새로 못 받음 — 마지막 값 표시)
  const [pointsState, setPointsState] = useState("idle");
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  useDismissibleMenu(open, setOpen, rootRef, triggerRef, pathname + search);

  useEffect(() => {
    if (!open) setKeyOpen(false);
  }, [open]);

  // 로그인 상태면 포인트 잔고를 서버에서 새로 받는다 — 다른 탭의 언락·적립이 상단바에 반영되게.
  useEffect(() => {
    if (!token) return undefined;
    let active = true;
    const requestedToken = token;
    setPointsState(getAuthUser()?.points_balance == null ? "loading" : "idle");
    api.me()
      .then((d) => {
        if (!active || getToken() !== requestedToken) return;
        updateAuthUser(d.user);
        setPointsState("idle");
      })
      .catch((reason) => {
        if (!active || getToken() !== requestedToken) return;
        if (reason.status === 401) clearAuth();
        else setPointsState("error");
      });
    return () => {
      active = false;
    };
  }, [token]);

  if (!token || !user) {
    if (["/login", "/forgot", "/reset"].includes(pathname)) {
      return null;
    }
    const next = encodeURIComponent(pathname + search);
    return (
      <div ref={rootRef} className="site-menu-root site-account-root">
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="site-ub site-ub-icon site-account-trigger is-anon"
          aria-expanded={open}
          aria-controls="site-account-menu"
          aria-label="계정 메뉴 · 로그인하지 않음"
        >
          <span className="site-avatar is-anon" aria-hidden="true"><UserIcon /></span>
          <span className="site-ub-tip" aria-hidden="true">로그인</span>
        </button>
        {open ? (
          <div id="site-account-menu" className="site-account-panel">
            <div className="site-account-head">
              <span className="site-avatar is-anon is-lg" aria-hidden="true"><UserIcon /></span>
              <div><strong>로그인하지 않았어요</strong><span>매크로 저장·포인트·채팅은 회원만 쓸 수 있어요</span></div>
            </div>
            <button type="button" className="btn btn-m btn-secondary site-account-cta" onClick={() => { setOpen(false); navigate(`/login?next=${next}`); }}>
              로그인
            </button>
            <button type="button" className="site-account-item" onClick={() => { setOpen(false); navigate(`/login?mode=signup&next=${next}`); }}>
              <strong>회원가입</strong><span>이메일·아이디·비밀번호면 돼요</span>
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  const points = (user.points_balance ?? 0).toLocaleString();
  const initial = String(user.username || "?").trim().charAt(0).toUpperCase() || "?";
  const pointsStale = pointsState === "error";
  return (
    <div ref={rootRef} className="site-menu-root site-account-root">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="site-ub site-account-trigger"
        data-state={pointsState === "idle" ? undefined : pointsState}
        title={pointsStale ? "포인트를 새로 불러오지 못했어요. 마지막으로 본 값이에요." : undefined}
        aria-expanded={open}
        aria-controls="site-account-menu"
        aria-label={`계정 메뉴 · ${user.username} · ${points}포인트${pointsStale ? " (새로 불러오지 못함)" : ""}`}
      >
        <span className="site-avatar" aria-hidden="true">{initial}</span>
        <span className="site-account-points num" aria-hidden="true">{points}P</span>
        <span className="site-ub-tip" aria-hidden="true">{user.username}</span>
      </button>
      {open ? (
        <div id="site-account-menu" className="site-account-panel">
          <div className="site-account-head">
            <span className="site-avatar is-lg" aria-hidden="true">{initial}</span>
            <div>
              <strong>{user.username}</strong>
              <span><b className="num">{points}P</b> · 포인트{pointsStale ? " · 새로 불러오지 못함" : ""}</span>
            </div>
          </div>
          <button type="button" className="site-account-item" onClick={() => { setOpen(false); navigate("/mypage"); }}>
            <strong>내 활동</strong><span>등록한 매크로와 포인트</span>
          </button>
          <button type="button" className="site-account-item" onClick={() => { setOpen(false); navigate("/agents"); }}>
            <strong>내 에이전트</strong><span>매크로 실행과 상태 관리</span>
          </button>
          <button
            type="button"
            className={`site-account-item${keyOpen ? " is-open" : ""}`}
            onClick={() => setKeyOpen((value) => !value)}
            aria-expanded={keyOpen}
            aria-controls="site-account-key"
          >
            <strong><KeyIcon />회원키</strong><span>실행기 ④번 칸에 넣는 키</span>
          </button>
          {keyOpen ? (
            <div id="site-account-key" className="site-account-key">
              <RunnerKeyPanel enabled={keyOpen} />
            </div>
          ) : null}
          <div className="site-account-sep" role="separator" />
          <button type="button" className="site-account-item" onClick={() => { setOpen(false); clearAuth(); navigate("/"); }}>
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
      : pathname.startsWith("/runner/install")
      ? "실행기 설치"
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
              <Route path="/runner/install" element={<RunnerInstall />} />
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
