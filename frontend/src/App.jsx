import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import Home from "./pages/Home.jsx";
// [차후 도입] 고래 동향 배너 — 거래소/컨트랙트 지갑 노이즈 정리 후 켤 예정.
// 컴포넌트와 백엔드(app/whales.py)는 그대로 두고 마운트만 꺼둡니다.
// import WhaleBanner from "./components/WhaleBanner.jsx";
import HotCoinsMarquee from "./components/HotCoinsMarquee.jsx";
import SiteNavigation from "./components/SiteNavigation.jsx";
import SiteHeader from "./components/SiteHeader.jsx";

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
const Guide = lazy(() => import("./pages/Guide.jsx"));

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
      ? "사용법"
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

function HomeRoute() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  const onboardingOpen = params.get("guide") === "1"
    || params.has("tour")
    || params.get("resume") === "hero-register";
  if (!params.has("help") || onboardingOpen) return <Home />;

  const next = new URLSearchParams();
  const section = params.get("help");
  if (section && section !== "start") next.set("section", section);
  if (params.has("q")) next.set("q", params.get("q"));
  const query = next.toString();
  return <Navigate to={{ pathname: "/guide", search: query ? `?${query}` : "" }} replace />;
}

function LegacyRunnerRedirect() {
  const location = useLocation();
  const params = new URLSearchParams(location.search);
  params.delete("help");
  params.set("run", "1");
  if (!params.has("step")) params.set("step", "1");
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
  const isLegacyStart = pathname === "/runner";
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
        <SiteHeader
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
              <Route path="/" element={<HomeRoute />} />
              <Route path="/builder" element={<Studio />} />
              <Route path="/s/:slug" element={<Studio />} />
              <Route path="/mypage" element={<MyPage />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/runner/install" element={<RunnerInstall />} />
              <Route path="/runner" element={<LegacyRunnerRedirect />} />
              <Route path="/guide" element={<Guide />} />
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
