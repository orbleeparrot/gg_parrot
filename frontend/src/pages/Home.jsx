import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  beginJourney,
  dismissJourney,
} from "../lib/journey.js";
import { lockBodyScroll } from "../lib/bodyScrollLock.js";
import { isLoggedIn } from "../lib/auth.js";

const StartGuide = lazy(() => import("./Start.jsx"));
const RunnerFlow = lazy(() => import("./RunnerDownload.jsx"));
const GuidePage = lazy(() => import("./Guide.jsx"));
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function HomeEntryHero({ onLeaderboard, onGuide }) {
  return (
    <section className="home-entry-hero" aria-labelledby="home-entry-title">
      <div className="home-entry-copy">
        <h1 id="home-entry-title">
          쉽게 시작하는 코인 매크로 커뮤니티 <span>껄무새</span>
        </h1>
        <p className="home-entry-description">
          코린이도 코드 없이 매크로를 만들고, 테스트넷 모의투자로 겨뤄요.
          실시간 채팅과 게시판에서 전략을 나누며 투자와 자연스럽게 친해지는 커뮤니티예요.
        </p>
        <ul className="home-entry-highlights" aria-label="껄무새에서 할 수 있는 것">
          <li><strong>코드 없이 매크로</strong><span>종목·전략·조건만 골라 바로 완성해요.</span></li>
          <li><strong>모의투자 경쟁</strong><span>테스트넷 수익률로 리더보드에서 겨뤄요.</span></li>
          <li><strong>실시간 채팅</strong><span>결과를 함께 보며 바로 묻고 답해요.</span></li>
          <li><strong>게시판 소통</strong><span>전략과 후기를 글·답글로 나눠요.</span></li>
        </ul>
        <p className="home-entry-note">웹의 백테스트와 모의 결과는 투자 조언이 아니에요.</p>
      </div>

      <nav className="home-entry-choices" aria-labelledby="home-entry-choice-title">
        <div className="home-entry-choice-heading">
          <h2 id="home-entry-choice-title">어떻게 시작할까요?</h2>
        </div>
        <button
          type="button"
          data-home-entry-primary
          onClick={onLeaderboard}
          className="home-entry-choice is-primary"
        >
          <span><strong>리더보드</strong><small>커뮤니티 인기 전략을 골라 바로 실행해요. 마음에 드는 매크로를 그대로 실행기로 돌릴 수 있어요.</small></span>
          <span aria-hidden="true">→</span>
        </button>
        <button
          type="button"
          data-home-guide-trigger
          aria-haspopup="dialog"
          onClick={onGuide}
          className="home-entry-choice is-primary"
        >
          <span><strong>직접 만들기</strong><small>안내를 따라 종목 검색부터 전략·조건·백테스트·등록까지 순서대로 내 매크로를 만들어요.</small></span>
          <span aria-hidden="true">→</span>
        </button>
      </nav>
    </section>
  );
}

export default function Home() {
  const [searchParams, setSearchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [nestedDialogOpen, setNestedDialogOpen] = useState(false);
  const backgroundRef = useRef(null);
  const dialogRef = useRef(null);
  const previousFocusRef = useRef(null);
  const previousRunnerOpenRef = useRef(false);
  const nestedDialogRef = useRef(false);
  nestedDialogRef.current = nestedDialogOpen;

  const explicitGuide = searchParams.get("guide") === "1";
  const legacyTour = searchParams.has("tour");
  const resumeRegistration = searchParams.get("resume") === "hero-register";
  const guideOpen = explicitGuide || legacyTour || resumeRegistration;
  const helpSection = searchParams.get("help") || "";
  const docsOpen = !guideOpen && !!helpSection;
  const overlayOpen = guideOpen || docsOpen;
  const runnerOpen = searchParams.get("run") === "1" || searchParams.has("step");
  const helpReturnTo = typeof location.state?.helpReturnTo === "string"
    ? location.state.helpReturnTo
    : "";

  const openRunner = useCallback((view) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("run", "1");
      next.set("step", "1");
      if (view) next.set("view", view);
      else next.delete("view");
      next.delete("help");
      next.delete("guide");
      next.delete("tour");
      next.delete("resume");
      return next;
    });
  }, [setSearchParams]);

  // 홈 '리더보드' 갈림길 — 실행 플로우를 리더보드 선택 화면에서 바로 연다.
  const openLeaderboardRun = useCallback(() => openRunner("leaderboard"), [openRunner]);

  // 로그아웃 상태에서 홈 진입 버튼을 누르면 바로 로그인 화면으로 보낸다.
  const requireLogin = useCallback((nextPath) => {
    const params = new URLSearchParams();
    params.set("next", nextPath);
    params.set("notice", "로그인 후 이용할 수 있어요.");
    navigate(`/login?${params.toString()}`);
  }, [navigate]);

  const closeRunner = useCallback(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("run");
      next.delete("step");
      next.delete("help");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  const openGuide = useCallback(() => {
    beginJourney();
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("guide", "1");
      next.set("tour", "build");
      next.delete("help");
      next.delete("resume");
      return next;
    });
  }, [setSearchParams]);

  // 리더보드·직접 만들기 진입은 로그인 필수 — 로그아웃이면 로그인 화면으로 보낸다.
  const startLeaderboard = useCallback(() => {
    if (!isLoggedIn()) { requireLogin("/?run=1&step=1&view=leaderboard"); return; }
    openLeaderboardRun();
  }, [openLeaderboardRun, requireLogin]);

  const startGuide = useCallback(() => {
    if (!isLoggedIn()) { requireLogin("/?guide=1&tour=build"); return; }
    openGuide();
  }, [openGuide, requireLogin]);

  const closeOverlay = useCallback(() => {
    if (guideOpen) {
      dismissJourney();
    }
    if (
      docsOpen
      && helpReturnTo.startsWith("/")
      && !helpReturnTo.startsWith("//")
    ) {
      navigate(helpReturnTo, { replace: true });
      return;
    }
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("guide");
      next.delete("tour");
      next.delete("resume");
      next.delete("help");
      return next;
    }, { replace: true });
  }, [docsOpen, guideOpen, helpReturnTo, navigate, setSearchParams]);

  const restartGuide = useCallback(() => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("guide", "1");
      next.set("tour", "build");
      next.delete("help");
      next.delete("resume");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  useEffect(() => {
    document.title = guideOpen
      ? "껄무새 가이드라인"
      : docsOpen
        ? "사용 방법 · 껄무새"
        : runnerOpen
          ? "매크로 만들기 가이드 · 껄무새"
          : "비트코인 매크로 · 껄무새";
  }, [docsOpen, guideOpen, runnerOpen]);

  useEffect(() => {
    if (guideOpen) beginJourney();
  }, [guideOpen]);

  useEffect(() => {
    const wasRunnerOpen = previousRunnerOpenRef.current;
    previousRunnerOpenRef.current = runnerOpen;
    if (!wasRunnerOpen || runnerOpen) return undefined;
    const frame = window.requestAnimationFrame(() => {
      document.querySelector("[data-home-entry-primary]")?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [runnerOpen]);

  useEffect(() => {
    const background = backgroundRef.current;
    const shellControls = Array.from(document.querySelectorAll([
      ".site-header .site-mobile-menu-button",
      ".site-header .market-context-topbar",
      ".site-header .site-header-utility",
      ".site-sidebar .site-side-nav",
      ".site-sidebar .site-sidebar-bottom",
      ".site-mobile-drawer",
    ].join(", ")));
    const chromeState = shellControls.map((element) => ({
      element,
      inert: element.inert,
      ariaHidden: element.getAttribute("aria-hidden"),
    }));
    if (background) background.inert = overlayOpen;
    if (overlayOpen) {
      shellControls.forEach((element) => {
        element.inert = true;
        element.setAttribute("aria-hidden", "true");
      });
    }
    return () => {
      if (background) background.inert = false;
      chromeState.forEach(({ element, inert, ariaHidden }) => {
        element.inert = inert;
        if (ariaHidden == null) element.removeAttribute("aria-hidden");
        else element.setAttribute("aria-hidden", ariaHidden);
      });
    };
  }, [overlayOpen]);

  useEffect(() => {
    if (!overlayOpen) return undefined;
    document.documentElement.classList.add("has-onboarding-overlay");
    previousFocusRef.current = document.activeElement;
    const unlockBodyScroll = lockBodyScroll();
    const frame = window.requestAnimationFrame(() => dialogRef.current?.focus({ preventScroll: true }));

    const onKeyDown = (event) => {
      if (nestedDialogRef.current) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeOverlay();
        return;
      }
      if (event.key !== "Tab") return;
      const dialog = dialogRef.current;
      const focusable = Array.from(dialog?.querySelectorAll(FOCUSABLE) || []);
      if (focusable.length === 0) {
        event.preventDefault();
        dialog?.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (!dialog?.contains(active) || active === dialog) {
        event.preventDefault();
        (event.shiftKey ? last : first).focus();
      } else if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.documentElement.classList.remove("has-onboarding-overlay");
      unlockBodyScroll();
      const previous = previousFocusRef.current;
      const fallback = previous?.isConnected && previous !== document.body
        ? previous
        : document.querySelector("[data-home-entry-primary], [data-home-guide-trigger]");
      window.requestAnimationFrame(() => fallback?.focus?.({ preventScroll: true }));
    };
  }, [closeOverlay, overlayOpen]);

  return (
    <>
      <div
        ref={backgroundRef}
        className={`home-start-page ${runnerOpen ? "is-runner" : "is-entry"}`}
        aria-hidden={overlayOpen ? "true" : undefined}
      >
        {runnerOpen ? (
          <Suspense fallback={<div className="home-start-loading t-small text-slate-600" role="status">매크로 화면 불러오는 중…</div>}>
            <RunnerFlow embedded onExit={closeRunner} />
          </Suspense>
        ) : (
          <HomeEntryHero onLeaderboard={startLeaderboard} onGuide={startGuide} />
        )}
      </div>

      {overlayOpen ? (
        <div className="onboarding-layer">
          <section
            ref={dialogRef}
            role="dialog"
            aria-modal={nestedDialogOpen ? undefined : "true"}
            aria-labelledby="onboarding-dialog-title"
            tabIndex={-1}
            className={`onboarding-dialog ${docsOpen ? "is-docs" : ""}`.trim()}
          >
            <header className="onboarding-dialog-bar">
              {guideOpen ? (
                <button
                  type="button"
                  className="onboarding-dialog-title-button min-w-0"
                  onClick={restartGuide}
                  aria-label="껄무새 가이드라인 첫 화면으로 이동"
                  title="가이드 처음으로"
                >
                  <span id="onboarding-dialog-title" className="t-title text-slate-900">껄무새 가이드라인</span>
                </button>
              ) : (
                <span id="onboarding-dialog-title" className="t-title text-slate-900">사용 방법</span>
              )}
              <button type="button" onClick={closeOverlay} className="onboarding-close" aria-label={guideOpen ? "껄무새 가이드라인 닫기" : "사용 방법 닫기"}>
                <span className="hidden sm:inline">{guideOpen ? "나중에 이어보기" : "닫기"}</span>
                <span aria-hidden="true">×</span>
              </button>
            </header>
            <div className={`onboarding-tour-viewport ${docsOpen ? "is-docs" : ""}`.trim()}>
              <Suspense fallback={<div className="onboarding-loading t-small text-slate-600" role="status">화면 불러오는 중…</div>}>
                {guideOpen ? (
                  <StartGuide onNestedDialogChange={setNestedDialogOpen} />
                ) : (
                  <GuidePage embedded initialSection={helpSection || "start"} />
                )}
              </Suspense>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
