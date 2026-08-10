import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  beginJourney,
  dismissJourney,
  readJourneyState,
} from "../lib/journey.js";
import { GUIDE_CHAPTERS } from "../lib/guideFlow.js";
import { lockBodyScroll } from "../lib/bodyScrollLock.js";

const StartGuide = lazy(() => import("./Start.jsx"));
const FOCUSABLE =
  'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const PRODUCT_STEPS = [
  {
    number: "01",
    title: "차트를 먼저 찾아요",
    body: "원하는 종목을 검색하고 실시간 차트를 본 뒤 A–K 전략과 조건을 정해요.",
  },
  {
    number: "02",
    title: "과거와 현재로 검증해요",
    body: "서버 백테스트와 페이퍼 트레이딩이 같은 설정을 이어받아 동작해요.",
  },
  {
    number: "03",
    title: "결과를 공개하고 비교해요",
    body: "검증한 설정만 오늘의 리더보드에 등록하고 채팅과 게시판에서 이야기해요.",
  },
];

function preloadGuide() {
  void import("./Start.jsx");
}

function ProductPreview() {
  return (
    <div className="home-product-preview" aria-label="껄무새 제품 흐름 예시">
      <div className="home-preview-topline">
        <span className="t-caption num text-slate-500">MACRO / 01</span>
        <span className="badge badge-flat">실제 주문 없음</span>
      </div>
      <div className="home-preview-rule">
        <div>
          <span className="t-caption text-slate-500">선택한 규칙</span>
          <p className="mt-2 t-h4 text-slate-900">SOL · 정기 분할매수</p>
        </div>
        <span className="num t-small text-slate-700">C</span>
      </div>
      <p className="home-preview-sentence text-slate-700">
        <span className="num font-bold text-slate-900">7일</span>마다
        <span className="num font-bold text-slate-900"> 100 USDT</span>씩 나누어 사고,
        <span className="num font-bold text-slate-900"> -3%</span>에서 손실을 제한해요.
      </p>
      <ol className="home-preview-pipeline" aria-label="제품 실행 단계">
        <li className="is-complete">
          <span className="num">01</span>
          <span><strong>백테스트</strong><small>실제 서버 계산</small></span>
          <span className="home-preview-status">확인</span>
        </li>
        <li>
          <span className="num">02</span>
          <span><strong>페이퍼 트레이딩</strong><small>현재 시세 모의 실행</small></span>
          <span className="home-preview-status">다음</span>
        </li>
        <li>
          <span className="num">03</span>
          <span><strong>리더보드</strong><small>오늘의 결과 등록</small></span>
          <span className="home-preview-status">대기</span>
        </li>
      </ol>
      <div className="home-preview-foot">
        <span>제품 흐름 예시</span>
        <span className="num">A–K · 11가지 전략</span>
      </div>
    </div>
  );
}

export default function Home() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [journeyState, setJourneyState] = useState(readJourneyState);
  const [nestedDialogOpen, setNestedDialogOpen] = useState(false);
  const backgroundRef = useRef(null);
  const dialogRef = useRef(null);
  const previousFocusRef = useRef(null);
  const nestedDialogRef = useRef(false);
  nestedDialogRef.current = nestedDialogOpen;

  const explicitGuide = searchParams.get("guide") === "1";
  const legacyTour = searchParams.has("tour");
  const resumeRegistration = searchParams.get("resume") === "hero-register";
  const guideOpen = explicitGuide || legacyTour || resumeRegistration;
  const hasUsedGuide = !!(
    journeyState.started_at || journeyState.dismissed_at || journeyState.completed_at
  );
  const guideLabel = journeyState.completed_at
    ? "가이드 다시 보기"
    : hasUsedGuide
    ? "가이드 이어보기"
    : "가이드로 첫 매크로 만들기";

  const openGuide = useCallback(() => {
    beginJourney();
    setJourneyState(readJourneyState());
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.set("guide", "1");
      next.set(
        "tour",
        !journeyState.completed_at && journeyState.last_tour
          ? journeyState.last_tour
          : "build"
      );
      next.delete("resume");
      return next;
    });
  }, [journeyState.completed_at, journeyState.last_tour, setSearchParams]);

  const closeGuide = useCallback(() => {
    dismissJourney();
    setJourneyState(readJourneyState());
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("guide");
      next.delete("tour");
      next.delete("resume");
      return next;
    }, { replace: true });
  }, [setSearchParams]);

  useEffect(() => {
    document.title = guideOpen
      ? "빠른 시작 가이드 · 껄무새"
      : "껄무새 · 매매 규칙을 만들고 검증해요";
  }, [guideOpen]);

  useEffect(() => {
    if (guideOpen) beginJourney();
    setJourneyState(readJourneyState());
  }, [guideOpen]);

  useEffect(() => {
    const background = backgroundRef.current;
    const shellChrome = Array.from(document.querySelectorAll(".site-header, .site-sidebar, .site-mobile-drawer"));
    const chromeState = shellChrome.map((element) => ({
      element,
      inert: element.inert,
      ariaHidden: element.getAttribute("aria-hidden"),
    }));
    if (background) background.inert = guideOpen;
    if (guideOpen) {
      shellChrome.forEach((element) => {
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
  }, [guideOpen]);

  useEffect(() => {
    if (!guideOpen) return undefined;
    previousFocusRef.current = document.activeElement;
    const unlockBodyScroll = lockBodyScroll();
    const frame = window.requestAnimationFrame(() => dialogRef.current?.focus({ preventScroll: true }));

    const onKeyDown = (event) => {
      if (nestedDialogRef.current) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeGuide();
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
      unlockBodyScroll();
      const previous = previousFocusRef.current;
      const fallback = previous?.isConnected && previous !== document.body
        ? previous
        : document.querySelector('[data-home-guide-trigger="hero"]');
      window.requestAnimationFrame(() => fallback?.focus?.({ preventScroll: true }));
    };
  }, [closeGuide, guideOpen]);

  return (
    <>
      <div
        ref={backgroundRef}
        className="home-page"
        aria-hidden={guideOpen ? "true" : undefined}
      >
        <section className="home-hero" aria-labelledby="home-hero-title">
          <div className="home-hero-copy">
            <p className="home-eyebrow">코딩 없이 만드는 코인 매매 규칙</p>
            <h1 id="home-hero-title" className="home-hero-title text-slate-900">
              감 대신 규칙으로<br />
              <mark>매매하고 검증해요.</mark>
            </h1>
            <p className="home-hero-description text-slate-600">
              원하는 종목의 실시간 차트를 먼저 보고 조건을 정하면, 과거 데이터 검증부터
              페이퍼 트레이딩과 오늘의 리더보드까지 하나의 설정으로 이어져요.
            </p>
            <div className="home-hero-actions">
              {hasUsedGuide ? (
                <>
                  <Link to="/builder" className="btn btn-xl btn-primary">매크로 바로 만들기</Link>
                  <button
                    type="button"
                    data-home-guide-trigger="hero"
                    onClick={openGuide}
                    onPointerEnter={preloadGuide}
                    onFocus={preloadGuide}
                    className="btn btn-xl btn-secondary"
                  >
                    {guideLabel}
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    data-home-guide-trigger="hero"
                    onClick={openGuide}
                    onPointerEnter={preloadGuide}
                    onFocus={preloadGuide}
                    className="btn btn-xl btn-primary"
                  >
                    {guideLabel}
                  </button>
                  <Link to="/builder" className="btn btn-xl btn-secondary">빌더 바로 열기</Link>
                </>
              )}
            </div>
            <p className="home-hero-note text-slate-500">
              웹 화면은 실제 주문을 보내지 않아요. 백테스트와 모의 결과는 투자 조언이 아니에요.
            </p>
          </div>
          <ProductPreview />
        </section>

        <section className="home-fact-strip" aria-label="껄무새 핵심 기능">
          <div><strong className="num">11</strong><span>A–K 전체 전략</span></div>
          <div><strong>서버 계산</strong><span>백테스트 결과</span></div>
          <div><strong>현재 시세</strong><span>페이퍼 트레이딩</span></div>
          <div><strong>매일 집계</strong><span>리더보드·채팅</span></div>
        </section>

        <section className="home-workflow" aria-labelledby="home-workflow-title">
          <div className="home-section-intro">
            <p className="home-eyebrow">하나의 규칙, 하나의 흐름</p>
            <h2 id="home-workflow-title" className="t-h2 text-slate-900">
              입력은 한 번, 검증은 끝까지.
            </h2>
            <p className="mt-4 t-body text-slate-600 measure">
              화면마다 설정을 다시 옮기지 않아요. 검색한 차트의 종목과 봉 간격, 조건이 검증과 등록까지 이어져요.
            </p>
          </div>
          <ol className="home-workflow-list">
            {PRODUCT_STEPS.map((step) => (
              <li key={step.number}>
                <span className="num home-workflow-number">{step.number}</span>
                <h3 className="t-h4 text-slate-900">{step.title}</h3>
                <p className="t-small text-slate-600">{step.body}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="home-guide-invite" aria-labelledby="home-guide-title">
          <div>
            <p className="home-eyebrow">직접 따라 하는 빠른 시작</p>
            <h2 id="home-guide-title" className="t-h2 text-slate-900">
              설명만 읽지 말고,<br />직접 등록까지 해보세요.
            </h2>
            <p className="mt-4 t-body text-slate-600 measure">
              원하는 종목의 실시간 차트를 확인하며 조건을 정하고, 실제 백테스트와 페이퍼 트레이딩을 거쳐 리더보드에 등록해요.
            </p>
            <button
              type="button"
              onClick={openGuide}
              onPointerEnter={preloadGuide}
              onFocus={preloadGuide}
              className="mt-7 btn btn-l btn-primary"
            >
              {guideLabel}
            </button>
          </div>
          <ol className="home-guide-chapters" aria-label={`빠른 가이드 ${GUIDE_CHAPTERS.length}단계`}>
            {GUIDE_CHAPTERS.map((chapter, index) => (
              <li key={chapter}>
                <span className="num">{String(index + 1).padStart(2, "0")}</span>
                <span>{chapter}</span>
              </li>
            ))}
          </ol>
        </section>

        <footer className="home-footer t-caption text-slate-500">
          <span>껄무새 · GGparrot</span>
          <span>모든 결과는 모의 계산이며 수익을 보장하지 않아요.</span>
        </footer>
      </div>

      {guideOpen ? (
        <div className="onboarding-layer">
          <section
            ref={dialogRef}
            role="dialog"
            aria-modal={nestedDialogOpen ? undefined : "true"}
            aria-labelledby="onboarding-dialog-title"
            tabIndex={-1}
            className="onboarding-dialog"
          >
            <header className="onboarding-dialog-bar">
              <div className="min-w-0">
                <p className="t-caption num text-slate-500">GGPARROT / QUICK START</p>
                <p id="onboarding-dialog-title" className="t-label text-slate-900">직접 따라 하는 빠른 시작</p>
              </div>
              <button type="button" onClick={closeGuide} className="onboarding-close" aria-label="빠른 시작 가이드 닫기">
                <span className="hidden sm:inline">나중에 이어보기</span>
                <span aria-hidden="true">×</span>
              </button>
            </header>
            <div className="onboarding-tour-viewport">
              <Suspense fallback={<div className="onboarding-loading t-small text-slate-600" role="status">가이드 불러오는 중…</div>}>
                <StartGuide onNestedDialogChange={setNestedDialogOpen} />
              </Suspense>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}
