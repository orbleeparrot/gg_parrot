import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
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
    <section
      className="home-entry-hero"
      aria-labelledby="home-entry-title"
      aria-roledescription="슬라이드"
      aria-label="1 / 2"
    >
      <div className="home-entry-copy">
        <h1 id="home-entry-title">
          쉽게 시작하는 코인 매크로 커뮤니티 <span>껄무새</span>
        </h1>
        <p className="home-entry-description">
          리더보드에서 마음에 드는 매크로를 골라 바로 실행하거나,
          종목과 조건을 직접 정해 내 전략을 만들어요. 처음이어도 테스트넷 실행까지 차근차근 이어져요.
        </p>
        <ul className="home-entry-highlights" aria-label="껄무새에서 할 수 있는 것">
          <li><strong>리더보드에서 선택</strong><span>다른 사용자의 전략과 모의 성과를 비교해요.</span></li>
          <li><strong>조건 직접 설정</strong><span>종목·전략·수치를 고르면 매크로가 완성돼요.</span></li>
          <li><strong>백테스트 확인</strong><span>과거 데이터로 수익과 위험을 먼저 살펴봐요.</span></li>
          <li><strong>테스트넷 실행</strong><span>실제 자금 없이 내 PC 실행기로 연습해요.</span></li>
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
          data-home-carousel-primary
          onClick={onLeaderboard}
          className="home-entry-choice is-primary"
        >
          <span className="home-entry-choice-art" aria-hidden="true">
            <img src="/brand/navigation/ggparrot-nav-leaderboard.png" alt="" width="88" height="88" draggable="false" />
          </span>
          <span className="home-entry-choice-copy"><strong>리더보드</strong><small>커뮤니티 인기 전략을 골라 바로 실행해요. 마음에 드는 매크로를 그대로 실행기로 돌릴 수 있어요.</small></span>
          <span className="home-entry-choice-arrow" aria-hidden="true">→</span>
        </button>
        <button
          type="button"
          data-home-guide-trigger
          aria-haspopup="dialog"
          onClick={onGuide}
          className="home-entry-choice"
        >
          <span className="home-entry-choice-art" aria-hidden="true">
            <img src="/brand/navigation/ggparrot-nav-builder.png" alt="" width="88" height="88" draggable="false" />
          </span>
          <span className="home-entry-choice-copy"><strong>직접 만들기</strong><small>안내를 따라 종목 검색부터 전략·조건·백테스트·등록까지 순서대로 내 매크로를 만들어요.</small></span>
          <span className="home-entry-choice-arrow" aria-hidden="true">→</span>
        </button>
      </nav>
    </section>
  );
}

const COMMUNITY_POSTS = [
  { title: "DCA 매수 간격 7일·14일 비교해 봤어요", snippet: "같은 SOLUSDT 조건에서 기간만 바꿔 본 결과와 느낀 점을 정리했습니다.", author: "차분한고래", time: "오늘 09:44", comments: 12 },
  { title: "백테스트 수익률보다 MDD를 먼저 봐야 하나요?", snippet: "첫 전략을 만들었는데 수익률은 높고 최대낙폭도 커서 기준이 궁금해요.", author: "코린이7일차", time: "오늘 09:08", comments: 8 },
  { title: "횡보장에서 그리드 간격 정하는 방법", snippet: "너무 촘촘하게 잡았을 때 수수료가 결과에 미친 영향을 비교했습니다.", author: "느린거북", time: "오늘 08:36", comments: 5 },
  { title: "페이퍼 트레이딩 첫날 기록", snippet: "실제 돈 없이 체결 흐름을 보니 조건이 언제 작동하는지 이해하기 쉬웠어요.", author: "보라여우", time: "어제 22:17", comments: 17, hasImage: true },
  { title: "이동평균 전략 기간을 바꿀 때 체크할 것", snippet: "20·60과 50·200 조합을 각각 돌려 본 표를 공유합니다.", author: "캔들읽는새", time: "어제 20:52", comments: 9 },
];

function CommunityPostList({ duplicate = false }) {
  return (
    <ul
      className="home-community-post-list"
      aria-hidden={duplicate ? "true" : undefined}
    >
      {COMMUNITY_POSTS.map((post) => (
        <li key={`${duplicate ? "loop-" : ""}${post.title}`}>
          <span className="home-community-post-copy">
            <strong>
              {post.hasImage ? <span className="home-board-photo-badge">사진</span> : null}
              {post.title}
              <span className="home-community-post-comments">[{post.comments}]</span>
            </strong>
            <small>{post.snippet}</small>
          </span>
          <span className="home-board-post-meta">
            <strong>{post.author}</strong>
            <time>{post.time}</time>
          </span>
        </li>
      ))}
    </ul>
  );
}

function CommunityEntryHero() {
  return (
    <section
      className="home-entry-hero is-community"
      aria-labelledby="home-community-title"
      aria-roledescription="슬라이드"
      aria-label="2 / 2"
    >
      <div className="home-entry-copy home-community-copy">
        <h1 id="home-community-title">
          매크로 이야기가 쌓이는 <span>껄무새 게시판.</span>
        </h1>
        <p className="home-entry-description">
          조건 설정이 막힐 때 다른 사용자의 질문과 답변을 찾아보고,
          백테스트 결과와 운영 후기를 글로 남겨 내 경험도 공유해요.
        </p>
        <dl className="home-community-points">
          <div>
            <dt>정보 찾아보기</dt>
            <dd>조건·백테스트·운영 기록을 주제별로 읽어봐요.</dd>
          </div>
          <div>
            <dt>경험 공유하기</dt>
            <dd>궁금한 점을 묻고 내 매크로의 시행착오를 남겨요.</dd>
          </div>
        </dl>
        <div className="home-community-actions" aria-label="커뮤니티 둘러보기">
          <Link to="/board" data-home-carousel-primary className="home-community-action is-primary">
            게시판 둘러보기 <span aria-hidden="true">→</span>
          </Link>
        </div>
      </div>

      <aside className="home-community-preview" aria-label="껄무새 게시판 화면 예시">
        <header className="home-board-preview-head">
          <span className="home-board-preview-mascot" aria-hidden="true">
            <img
              src="/brand/navigation/ggparrot-nav-board.png"
              alt=""
              width="256"
              height="256"
              draggable="false"
            />
          </span>
          <div>
            <span>전략·질문·정보</span>
            <h2>껄무새 게시판</h2>
            <p>코린이끼리 전략·질문·정보를 나눠요. (투자 조언 아님)</p>
          </div>
          <span className="home-board-preview-write" aria-hidden="true">새 글 쓰기</span>
        </header>
        <div className="home-community-post-viewport">
          <div className="home-community-post-track">
            <CommunityPostList />
            <CommunityPostList duplicate />
          </div>
        </div>
        <footer className="home-board-preview-footer" aria-hidden="true">
          <span>‹</span><strong>1</strong><span>2</span><span>3</span><span>›</span>
        </footer>
      </aside>
    </section>
  );
}

function HomeHeroRotator({ onLeaderboard, onGuide, paused = false }) {
  const [activeSlide, setActiveSlide] = useState(0);
  const [outgoingSlide, setOutgoingSlide] = useState(null);
  const [direction, setDirection] = useState(1);
  const [timerCycle, setTimerCycle] = useState(0);
  const [interactionPaused, setInteractionPaused] = useState(false);
  const [documentHidden, setDocumentHidden] = useState(false);
  const [reducedMotion, setReducedMotion] = useState(false);

  useEffect(() => {
    const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
    const syncPreferences = () => setReducedMotion(motionQuery.matches);
    syncPreferences();
    motionQuery.addEventListener?.("change", syncPreferences);
    return () => motionQuery.removeEventListener?.("change", syncPreferences);
  }, []);

  useEffect(() => {
    const syncVisibility = () => setDocumentHidden(document.hidden);
    syncVisibility();
    document.addEventListener("visibilitychange", syncVisibility);
    return () => document.removeEventListener("visibilitychange", syncVisibility);
  }, []);

  const autoRotationBlocked = paused
    || interactionPaused
    || documentHidden
    || reducedMotion;

  const selectSlide = useCallback((index, directionHint) => {
    setTimerCycle((current) => current + 1);
    if (index === activeSlide) return;
    setOutgoingSlide(activeSlide);
    setDirection(directionHint ?? (index > activeSlide ? 1 : -1));
    setActiveSlide(index);
  }, [activeSlide]);

  useEffect(() => {
    if (outgoingSlide == null) return undefined;
    const timer = window.setTimeout(() => setOutgoingSlide(null), 560);
    return () => window.clearTimeout(timer);
  }, [activeSlide, outgoingSlide]);

  useEffect(() => {
    if (autoRotationBlocked) return undefined;
    const timer = window.setTimeout(() => {
      selectSlide((activeSlide + 1) % 2, 1);
    }, 8000);
    return () => window.clearTimeout(timer);
  }, [activeSlide, autoRotationBlocked, selectSlide, timerCycle]);

  return (
    <div
      className="home-hero-shell"
      role="region"
      aria-roledescription="캐러셀"
      aria-label="껄무새 소개"
      onFocusCapture={() => setInteractionPaused(true)}
      onBlurCapture={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setInteractionPaused(false);
      }}
    >
      <div className="home-hero-rotator">
        <div className="home-hero-stage" aria-live="off">
          {[0, 1].map((index) => {
            const isActive = index === activeSlide;
            const isOutgoing = index === outgoingSlide && !isActive;
            if (!isActive && !isOutgoing) return null;
            const motionClass = direction > 0 ? "is-forward" : "is-backward";
            const phaseClass = isActive
              ? (outgoingSlide == null ? "is-active" : "is-active is-entering")
              : "is-exiting";
            return (
              <div
                key={index}
                className={`home-hero-slide-frame ${phaseClass} ${motionClass}`}
                aria-hidden={!isActive}
                inert={!isActive ? "" : undefined}
              >
                {index === 0 ? (
                  <HomeEntryHero onLeaderboard={onLeaderboard} onGuide={onGuide} />
                ) : (
                  <CommunityEntryHero />
                )}
              </div>
            );
          })}
        </div>
      </div>
      <nav className="home-hero-pagination" aria-label="홈 소개 화면 선택">
        <button
          type="button"
          className={activeSlide === 0 ? "is-active" : ""}
          aria-label="첫 번째 화면, 매크로 시작"
          aria-current={activeSlide === 0 ? "true" : undefined}
          onClick={() => selectSlide(0, -1)}
          onPointerUp={(event) => event.currentTarget.blur()}
        />
        <button
          type="button"
          className={activeSlide === 1 ? "is-active" : ""}
          aria-label="두 번째 화면, 커뮤니티"
          aria-current={activeSlide === 1 ? "true" : undefined}
          onClick={() => selectSlide(1, 1)}
          onPointerUp={(event) => event.currentTarget.blur()}
        />
      </nav>
    </div>
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
      document.querySelector("[data-home-carousel-primary], [data-home-entry-primary]")?.focus({ preventScroll: true });
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
        : document.querySelector("[data-home-carousel-primary], [data-home-entry-primary], [data-home-guide-trigger]");
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
          <HomeHeroRotator
            onLeaderboard={startLeaderboard}
            onGuide={startGuide}
            paused={overlayOpen}
          />
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
