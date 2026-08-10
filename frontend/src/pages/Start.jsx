import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import {
  AssetScene,
  BacktestScene,
  BuildScene,
  ChartScene,
  CommunityScene,
  ConditionWorkbench,
  GuideScene,
  NewsScene,
  PaperScene,
  RegisterScene,
  conditionError,
  getConditionFields,
} from "../components/HeroGuideScreens.jsx";
import useHeroBacktest, { macroFingerprint } from "../hooks/useHeroBacktest.js";
import usePaperSession from "../hooks/usePaperSession.js";
import { RULE_TYPES, buildMacro, defaultForm, macroToForm, validate } from "../lib/macro.js";
import { GUIDE_CHAPTERS as CHAPTERS } from "../lib/guideFlow.js";
import { api } from "../api.js";
import { getUserId } from "../lib/user.js";
import {
  completeJourney,
  peekRegistrationDraft,
  readHeroDraft,
  saveHeroDraft,
  saveJourneyStep,
  takeRegistrationDraft,
} from "../lib/journey.js";

const RegisterMacroModal = lazy(() => import("../components/RegisterMacroModal.jsx"));

const title = (before, accent, after) => ({ before, accent, after });

function initialForm() {
  const saved = readHeroDraft();
  if (!saved) return defaultForm();
  try {
    const restored = macroToForm(saved);
    if (restored.rule_type === "H") {
      restored.initial_capital = Math.max(Number(restored.initial_capital) || 0, 10000000);
    }
    return restored;
  } catch (_) {
    return defaultForm();
  }
}

function createScreens(form) {
  const ruleType = form.rule_type;
  const fields = getConditionFields(ruleType, form);
  const setup = [
    { id: "strategy", kind: "strategy", chapter: 4 },
    ...fields.map((field) => ({
      id: `condition-${ruleType}-${field.key}`,
      kind: "condition",
      chapter: 4,
      field,
    })),
    { id: "risk", kind: "risk", chapter: 4 },
    { id: "period", kind: "period", chapter: 4 },
  ].map((screen, index, all) => ({ ...screen, substep: index + 1, substepTotal: all.length }));

  return [
    { id: "build", kind: "build", chapter: 1 },
    { id: "asset", kind: "asset", chapter: 2 },
    { id: "chart", kind: "chart", chapter: 3 },
    ...setup,
    { id: "backtest", kind: "backtest", chapter: 5 },
    { id: "paper", kind: "paper", chapter: 6 },
    { id: "register", kind: "register", chapter: 7 },
    { id: "community", kind: "community", chapter: 8 },
    { id: "news", kind: "news", chapter: 9 },
    { id: "guide", kind: "guide", chapter: 10 },
  ];
}

function copyFor(screen) {
  switch (screen.kind) {
    case "build":
      return {
        eyebrow: "아이디어를 규칙으로",
        title: title("매크로는 ", "내가 정한 조건", "을 반복해요."),
        description: "먼저 거래 아이디어를 종목·전략·숫자로 나눠요. 이후 화면에서 한 가지씩 정하면 실제 실행 단계까지 같은 설정이 이어져요.",
      };
    case "asset":
      return {
        eyebrow: "종목 검색",
        title: title("원하는 ", "코인 차트", "부터 찾아요."),
        description: "직접 종목 코드를 검색하거나 대표 종목을 고르세요. 여기서 고른 종목이 차트와 이후 모든 조건에 그대로 이어져요.",
      };
    case "chart":
      return {
        eyebrow: "실시간 차트 확인",
        title: title("조건을 정하기 전에 ", "가격 흐름", "을 먼저 봐요."),
        description: "검색한 종목의 실제 실시간 캔들 차트를 확인해요. 봉 간격을 바꾸고 고가·저가·현재가를 살펴본 뒤 이 흐름에 맞는 조건을 정해요.",
      };
    case "strategy":
      return {
        eyebrow: "조건 설정",
        title: title("가격을 어떻게 볼지 ", "전략", "을 골라요."),
        description: "오른쪽에 검색한 종목의 차트를 계속 띄워두었어요. 가격 흐름과 작동 방식을 함께 보며 A–K 전략 중 하나를 골라요.",
      };
    case "condition":
      return {
        eyebrow: "조건 설정",
        title: title("이번에는 ", screen.field.label, "을 정해요."),
        description: "오른쪽 차트와 비교하면서 한 화면에서 숫자나 선택지 하나만 정해요. 다음 화면으로 넘어가도 차트와 앞에서 고른 값은 유지돼요.",
      };
    case "risk":
      return {
        eyebrow: "조건 설정",
        title: title("수익보다 먼저 ", "손실의 끝", "을 정해요."),
        description: "차트의 변동폭을 참고해 손실의 끝을 정해요. 손절은 한 번의 잘못된 판단이 커지는 것을 제한하는 규칙이에요.",
      };
    case "period":
      return {
        eyebrow: "조건 설정",
        title: title("어느 ", "과거 구간", "에서 확인할지 정해요."),
        description: "차트의 봉 간격과 함께 과거 구간을 정해요. 짧은 기간과 긴 기간은 서로 다른 결과를 보여줄 수 있어요.",
      };
    case "backtest":
      return {
        eyebrow: "과거 데이터 확인",
        title: title("수익과 위험을 ", "실제 데이터", "로 확인해요."),
        description: "앞에서 정한 설정을 서버에서 계산해요. 수익률과 최대낙폭, 홀딩 기준선, 매매 횟수와 자산곡선까지 기존 빌더와 같은 결과로 확인해요.",
      };
    case "paper":
      return {
        eyebrow: "실시간 모의 확인",
        title: title("이제 ", "현재 시세", "로 규칙을 지켜봐요."),
        description: "실제 돈이나 거래소 키 없이 페이퍼 세션을 시작해요. 선택한 전략의 현재 평가금액과 가상 체결 기록이 실제로 갱신돼요.",
      };
    case "register":
      return {
        eyebrow: "오늘의 리더보드",
        title: title("같은 설정을 ", "오늘의 순위", "에 올려요."),
        description: "백테스트와 페이퍼 확인을 마친 설정만 등록해요. 등록이 완료되면 오늘 자정까지 모의 수익률을 집계하는 새 세션이 시작돼요.",
      };
    case "community":
      return {
        eyebrow: "두 가지 대화 공간",
        title: title("바로 묻고, 오래 ", "남겨요", "."),
        description: "리더보드 채팅에서는 오늘의 결과를 보며 짧게 묻고 답해요. 게시판에는 전략 비교와 질문, 후기처럼 나중에도 다시 볼 이야기를 남겨요.",
      };
    case "news":
      return {
        eyebrow: "시장 맥락 확인",
        title: title("가격 밖의 ", "시장 움직임", "도 함께 읽어요."),
        description: "좌측 사이드바의 ‘코인동향’을 열면 시장·규제 헤드라인과 급등 종목 뉴스를 원문 출처와 함께 확인할 수 있어요. 뉴스는 매수 추천이 아니라 참고 정보예요.",
      };
    default:
      return {
        eyebrow: "둘러보기 완료",
        title: title("더 자세한 내용은 ", "가이드 페이지", "에서 확인해요."),
        description: "좌측 사이드바에서 ‘사용 가이드’를 누르면 전략별 작동 방식, 결과 읽기, 롱·숏과 레버리지 위험을 문서로 이어서 볼 수 있어요.",
      };
  }
}

function periodSummary(form) {
  if (form.preset === "custom") return `${form.start || "?"} ~ ${form.end || "?"}`;
  return { "3m": "최근 3개월", "6m": "최근 6개월", "1y": "최근 1년" }[form.preset] || "직접 지정";
}

function normalizeGuideSymbol(value) {
  const symbol = String(value || "").trim().toUpperCase();
  if (!symbol || symbol.endsWith("USDT")) return symbol;
  return `${symbol}USDT`;
}

function workspaceLabel(screen) {
  if (screen.kind === "asset") return "종목 검색";
  if (screen.kind === "chart") return "실시간 차트";
  if (screen.kind === "backtest") return "백테스트 결과";
  if (screen.kind === "paper") return "페이퍼 트레이딩";
  if (screen.kind === "register") return "리더보드 등록";
  if (screen.kind === "community") return "커뮤니티 미리보기";
  if (screen.kind === "news") return "코인동향 미리보기";
  if (screen.kind === "guide") return "사용 가이드 미리보기";
  if (screen.kind === "build") return "규칙 만드는 방식";
  return "매크로 조건 편집";
}

function workspaceStatus(screen) {
  if (["backtest", "paper", "register"].includes(screen.kind)) return null;
  if (screen.kind === "chart") return "바이낸스 공개 시세";
  if (["community", "news"].includes(screen.kind)) return "화면 목업";
  if (screen.kind === "guide") return "실제 문서";
  return "설정 유지됨";
}

function HeroScene({
  screen,
  form,
  setForm,
  macro,
  validationError,
  backtest,
  paperController,
  registrationEligible,
  registeredEntry,
  registeredBoard,
  boardLoading,
  boardError,
  onOpenRegister,
  chartLoadState,
}) {
  switch (screen.kind) {
    case "build":
      return <BuildScene form={form} />;
    case "asset":
      return (
        <AssetScene
          form={form}
          setForm={setForm}
          error={validationError}
          searchError={chartLoadState.searchError}
          busy={chartLoadState.searchBusy}
        />
      );
    case "chart":
      return <ChartScene form={form} setForm={setForm} onLoadState={chartLoadState.onLoadState} />;
    case "strategy":
    case "condition":
    case "risk":
    case "period":
      return <ConditionWorkbench screen={screen} form={form} setForm={setForm} error={validationError} />;
    case "backtest":
      return <BacktestScene form={form} backtest={backtest} />;
    case "paper":
      return (
        <PaperScene
          macro={backtest.resultIsFresh ? backtest.testedMacro : macro}
          valErr={backtest.resultIsFresh ? "" : "먼저 현재 설정으로 백테스트를 완료해 주세요."}
          controller={paperController}
        />
      );
    case "register":
      return (
        <RegisterScene
          macro={backtest.testedMacro || macro}
          summary={{ period: periodSummary(form) }}
          canRegister={registrationEligible}
          registeredEntry={registeredEntry}
          registeredBoard={registeredBoard}
          boardLoading={boardLoading}
          boardError={boardError}
          onOpen={onOpenRegister}
        />
      );
    case "community":
      return <CommunityScene />;
    case "news":
      return <NewsScene />;
    default:
      return <GuideScene />;
  }
}

function actionLabel(screen, backtest, paperController, paperReady, registrationReady, searchBusy, chartStatus) {
  switch (screen.kind) {
    case "build": return "종목 검색하기";
    case "asset": return searchBusy ? "종목 확인 중…" : "실시간 차트 확인";
    case "chart":
      if (chartStatus === "error") return "차트를 확인할 수 없어요";
      if (chartStatus !== "ready") return "차트 불러오는 중…";
      return "이 차트로 조건 설정하기";
    case "strategy": return "전략 조건 정하기";
    case "condition": return "다음 조건 정하기";
    case "risk": return "테스트 기간 정하기";
    case "period": return "백테스트 진행";
    case "backtest":
      if (backtest.currentBusy) return "결과 계산 중…";
      if (backtest.resultIsFresh) return "페이퍼 트레이딩 진행";
      if (backtest.result) return "바뀐 조건으로 다시 테스트";
      if (backtest.error) return "다시 백테스트";
      return "이 조건으로 백테스트";
    case "paper":
      if (paperController.busy) return "세션 처리 중…";
      return paperReady ? "세션을 마치고 등록하기" : "페이퍼 세션을 시작해 주세요";
    case "register": return registrationReady ? "리더보드 채팅·게시판 보기" : "등록을 완료해 주세요";
    case "community": return "코인동향 보기";
    case "news": return "마지막 안내 보기";
    default: return "가이드 페이지 열기";
  }
}

function screenError(screen, form) {
  if (screen.kind === "asset") {
    const symbols = form.symbol.split(",").map((value) => value.trim()).filter(Boolean);
    if (symbols.length === 0) return "종목을 입력해 주세요.";
    if (symbols.length > 1) return "빠른 가이드에서는 종목 하나만 입력해 주세요. 여러 종목은 전체 빌더에서 설정할 수 있어요.";
    if (!symbols.every((value) => /^[A-Z0-9]+$/.test(value))) return "종목은 영문과 숫자로 입력해 주세요.";
  }
  if (screen.kind === "condition") return conditionError(screen.field, form);
  if (screen.kind === "risk" && form.use_stop_loss && !(Number(form.stop_loss_pct) > 0)) {
    return "손절 기준은 0보다 큰 숫자로 입력해 주세요.";
  }
  if (screen.kind === "period") return validate(form) || "";
  return "";
}

function HeroTitle({ value }) {
  return (
    <>
      {value.before}<mark className="hero-title-highlight">{value.accent}</mark>{value.after}
    </>
  );
}

export default function Start({ onNestedDialogChange }) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const searchKey = searchParams.toString();
  const resumeRegistration = searchParams.get("resume") === "hero-register";
  const [form, setForm] = useState(initialForm);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registrationMode, setRegistrationMode] = useState("live");
  const [registered, setRegistered] = useState(null);
  const [registeredBoardState, setRegisteredBoardState] = useState(null);
  const [resumedKey, setResumedKey] = useState("");
  const [resumePendingKey, setResumePendingKey] = useState("");
  const [resumePhase, setResumePhase] = useState(() => resumeRegistration ? "restoring" : "idle");
  const [resumeError, setResumeError] = useState("");
  const [symbolSearchBusy, setSymbolSearchBusy] = useState(false);
  const [symbolSearchError, setSymbolSearchError] = useState("");
  const [chartLoad, setChartLoad] = useState({ symbol: "", interval: "", status: "idle", error: "" });
  const headingRef = useRef(null);
  const workRef = useRef(null);
  const resumeHandledRef = useRef(false);
  const resumeRequestIdRef = useRef(0);
  const boardRequestIdRef = useRef(0);
  const mountedRef = useRef(false);
  const previousScreenKindRef = useRef("");

  const setRegistrationLayer = useCallback((nextOpen) => {
    onNestedDialogChange?.(nextOpen);
    setRegisterOpen(nextOpen);
  }, [onNestedDialogChange]);

  const handleChartLoadState = useCallback((next) => {
    setChartLoad((current) => (
      current.symbol === next.symbol &&
      current.interval === next.interval &&
      current.status === next.status &&
      current.error === next.error
        ? current
        : next
    ));
  }, []);

  const syncRegisteredBoard = useCallback((entry, key, mode, showLoading = false) => {
    const requestId = ++boardRequestIdRef.current;
    setRegisteredBoardState((current) => {
      if (current?.key !== key || current?.mode !== mode) return current;
      return { ...current, requestId, loading: showLoading ? true : current.loading };
    });

    api.leaderboard(getUserId()).then((data) => {
      if (!mountedRef.current) return;
      const fetchedItems = Array.isArray(data.items) ? data.items : [];
      const synced = fetchedItems.some((item) => item.id === entry.id);
      const items = synced ? fetchedItems : [entry, ...fetchedItems];
      setRegisteredBoardState((current) => {
        if (current?.key !== key || current?.mode !== mode || current.requestId !== requestId) return current;
        return {
          key,
          mode,
          requestId,
          loading: false,
          error: !synced,
          data: { ...data, items },
        };
      });
    }).catch(() => {
      if (!mountedRef.current) return;
      setRegisteredBoardState((current) => {
        if (current?.key !== key || current?.mode !== mode || current.requestId !== requestId) return current;
        return { ...current, loading: false, error: true };
      });
    });
  }, []);

  const screens = useMemo(
    () => createScreens(form),
    [form.rule_type, form.exit_mode]
  );
  const requestedId = searchParams.get("tour") || "build";
  const requestedIndex = screens.findIndex((candidate) => candidate.id === requestedId);
  const currentIndex = requestedIndex >= 0 ? requestedIndex : 0;
  const screen = screens[currentIndex];
  const nextScreen = screens[currentIndex + 1] || null;
  const previousScreen = screens[currentIndex - 1] || null;
  const copy = copyFor(screen);
  const macro = useMemo(() => buildMacro(form), [form]);
  const currentKey = useMemo(() => macroFingerprint(macro), [macro]);
  const currentKeyRef = useRef(currentKey);
  const resumePendingKeyRef = useRef(resumePendingKey);
  const screenKindRef = useRef(screen.kind);
  currentKeyRef.current = currentKey;
  resumePendingKeyRef.current = resumePendingKey;
  screenKindRef.current = screen.kind;
  const builderState = useMemo(() => ({ macro, source: "hero-guide" }), [macro]);
  const backtest = useHeroBacktest(currentKey);
  const paperMacro = backtest.resultIsFresh ? backtest.testedMacro : macro;
  const paperController = usePaperSession({
    macro: paperMacro,
    valErr: backtest.resultIsFresh ? "" : "먼저 현재 설정으로 백테스트를 완료해 주세요.",
    stopOnUnmount: true,
  });
  const validationError = screenError(screen, form);
  const chartReady =
    chartLoad.status === "ready" &&
    chartLoad.symbol === normalizeGuideSymbol(form.symbol) &&
    chartLoad.interval === form.candle_interval;
  const paperReady =
    backtest.resultIsFresh &&
    paperController.startedKey === backtest.testedKey &&
    paperController.startedMode === paperController.mode;
  const registrationEligible =
    backtest.resultIsFresh &&
    (paperReady || resumedKey === currentKey) &&
    !paperController.running &&
    !paperController.busy;
  const registrationReady = registered?.key === currentKey && registered?.mode === registrationMode;
  const registeredEntry = registrationReady ? registered.entry : null;
  const boardStateIsCurrent = registeredBoardState?.key === currentKey && registeredBoardState?.mode === registrationMode;
  const registeredBoard = boardStateIsCurrent ? registeredBoardState.data : null;
  const boardLoading = boardStateIsCurrent && registeredBoardState.loading;
  const boardError = boardStateIsCurrent && registeredBoardState.error;
  const chapterName = CHAPTERS[screen.chapter - 1];
  const progress = (screen.chapter / CHAPTERS.length) * 100;

  useEffect(() => {
    saveHeroDraft(macro);
  }, [macro]);

  useEffect(() => {
    setSymbolSearchError("");
  }, [form.symbol]);

  useEffect(() => {
    saveJourneyStep(screen.id);
  }, [screen.id]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => () => onNestedDialogChange?.(false), [onNestedDialogChange]);

  useEffect(() => {
    if (screen.kind !== "register" || !registrationReady || !registeredEntry?.id) return undefined;
    const timer = window.setInterval(() => {
      syncRegisteredBoard(registeredEntry, currentKey, registered.mode);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [currentKey, registered?.mode, registeredEntry, registrationReady, screen.kind, syncRegisteredBoard]);

  useEffect(() => {
    if (screen.kind !== "register" || !registrationReady || !registeredEntry?.id) return undefined;
    const frame = window.requestAnimationFrame(() => {
      workRef.current?.scrollTo({ top: 0, left: 0, behavior: "auto" });
      document.getElementById("hero-registered-board")?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [registeredEntry?.id, registrationReady, screen.kind]);

  useEffect(() => {
    if (requestedIndex >= 0 || requestedId === "build") return;
    const next = new URLSearchParams(searchKey);
    if (requestedId.startsWith("condition-")) next.set("tour", "strategy");
    else next.delete("tour");
    setSearchParams(next, { replace: true });
  }, [requestedId, requestedIndex, searchKey, setSearchParams]);

  useEffect(() => {
    if (!resumeRegistration || resumeHandledRef.current) return;
    resumeHandledRef.current = true;
    setResumePhase("restoring");
    setResumeError("");

    const nextParams = new URLSearchParams(searchKey);
    nextParams.delete("resume");
    nextParams.set("tour", "register");
    setSearchParams(nextParams, { replace: true });

    const draft = peekRegistrationDraft();
    if (!draft?.macro || draft.context?.origin !== "hero") {
      setResumeError("로그인 전에 보관한 가이드 설정을 찾지 못했어요. 백테스트부터 다시 진행해 주세요.");
      setResumePhase("idle");
      return;
    }

    let restored;
    let restoredMacro;
    try {
      restored = macroToForm(draft.macro);
      restoredMacro = buildMacro(restored);
    } catch (_) {
      setResumeError("보관한 설정을 읽지 못했어요. 조건을 다시 정해 주세요.");
      setResumePhase("idle");
      return;
    }

    const restoredKey = macroFingerprint(restoredMacro);
    const restoredMode = draft.context?.mode === "replay" ? "replay" : "live";
    const requestId = ++resumeRequestIdRef.current;
    setForm(restored);
    setResumePendingKey(restoredKey);
    setRegistrationMode(restoredMode);
    paperController.setMode(restoredMode);

    backtest.run(restored).then((ok) => {
      if (!mountedRef.current || requestId !== resumeRequestIdRef.current) return;
      const contextIsCurrent =
        currentKeyRef.current === restoredKey &&
        resumePendingKeyRef.current === restoredKey &&
        screenKindRef.current === "register";
      if (!contextIsCurrent) {
        setResumePhase("idle");
        return;
      }
      if (ok) {
        takeRegistrationDraft();
        setResumedKey(restoredKey);
        setResumePendingKey("");
        setRegistrationLayer(true);
      } else {
        setResumeError("등록 전에 같은 설정으로 백테스트를 다시 확인하지 못했어요. 아래에서 다시 실행해 주세요.");
      }
      setResumePhase("idle");
    });
  }, [backtest.run, paperController.setMode, resumeRegistration, searchKey, setRegistrationLayer, setSearchParams]);

  useEffect(() => {
    if (resumePhase === "restoring") return;
    let target = "";
    if (screen.kind === "paper" && !backtest.resultIsFresh) target = "backtest";
    if (screen.kind === "register") {
      if (!backtest.resultIsFresh) target = "backtest";
      else if (!paperReady && resumedKey !== currentKey) target = "paper";
    }
    if (!target || requestedId === target) return;
    const next = new URLSearchParams(searchKey);
    next.set("tour", target);
    setSearchParams(next, { replace: true });
  }, [
    backtest.resultIsFresh,
    currentKey,
    paperReady,
    requestedId,
    resumePhase,
    resumedKey,
    screen.kind,
    searchKey,
    setSearchParams,
  ]);

  useEffect(() => {
    const previousKind = previousScreenKindRef.current;
    previousScreenKindRef.current = screen.kind;
    if (
      previousKind === "paper" &&
      screen.kind !== "paper" &&
      (paperController.running || paperController.phase === "starting")
    ) {
      void paperController.stop();
    }
  }, [paperController.phase, paperController.running, paperController.stop, screen.kind]);

  useEffect(() => {
    document.title = `${chapterName} · 시작 가이드 · 껄무새`;
    const frame = window.requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
    return () => window.cancelAnimationFrame(frame);
  }, [chapterName, screen.id]);

  function goTo(target) {
    const next = new URLSearchParams(searchKey);
    next.set("guide", "1");
    next.delete("resume");
    if (target.id === "build") next.delete("tour");
    else next.set("tour", target.id);
    setSearchParams(next, { replace: true });
  }

  async function advance() {
    if (screen.kind === "guide") {
      navigate("/guide");
      return;
    }
    if (validationError || !nextScreen) return;

    if (screen.kind === "asset") {
      const symbol = normalizeGuideSymbol(form.symbol);
      setSymbolSearchBusy(true);
      setSymbolSearchError("");
      try {
        const data = await api.candles(symbol, form.candle_interval, 300);
        if (!Array.isArray(data.candles) || data.candles.length === 0) throw new Error("NO_CANDLES");
        setForm((current) => ({ ...current, symbol }));
        goTo(nextScreen);
      } catch (reason) {
        setSymbolSearchError(
          reason?.status === 422
            ? "바이낸스 현물에서 이 종목을 찾지 못했어요. BTC 또는 BTCUSDT처럼 다시 검색해 주세요."
            : "지금 시세 서버에 연결하지 못했어요. 잠시 후 다시 확인해 주세요.",
        );
      } finally {
        setSymbolSearchBusy(false);
      }
      return;
    }

    if (screen.kind === "backtest" && !backtest.resultIsFresh) {
      const runKey = currentKey;
      const ok = await backtest.run(form);
      if (!ok || currentKeyRef.current !== runKey || screenKindRef.current !== "backtest") return;
      setResumeError("");
      if (resumePendingKeyRef.current === runKey) {
        takeRegistrationDraft();
        setResumedKey(runKey);
        setResumePendingKey("");
        const registerScreen = screens.find((candidate) => candidate.kind === "register");
        if (registerScreen) goTo(registerScreen);
        setRegistrationLayer(true);
      }
      return;
    }
    if (screen.kind === "paper") {
      if (!paperReady) return;
      const stopped = await paperController.stop();
      if (!stopped) return;
      setRegistrationMode(paperController.mode);
    }
    if (screen.kind === "register" && !registrationReady) return;
    goTo(nextScreen);
  }

  async function goPrevious() {
    if (!previousScreen) return;
    if (backtest.busy || resumePhase === "restoring") return;
    if (screen.kind === "paper" && paperController.running) {
      const stopped = await paperController.stop();
      if (!stopped) return;
    }
    goTo(previousScreen);
  }

  function openRegister() {
    if (!registrationEligible) return;
    setRegistrationMode(paperController.mode);
    setRegistrationLayer(true);
  }

  function finishRegistration(entry) {
    if (!entry?.id) {
      setResumeError("등록은 처리됐지만 결과 항목을 확인하지 못했어요. 잠시 후 리더보드에서 확인해 주세요.");
      return;
    }
    const registeredKey = backtest.testedKey;
    const registeredMode = entry.mode === "replay" ? "replay" : registrationMode;
    completeJourney();
    setResumeError("");
    setRegistered({ entry, key: registeredKey, mode: registeredMode });
    setRegisteredBoardState({
      key: registeredKey,
      mode: registeredMode,
      requestId: 0,
      loading: true,
      error: false,
      data: { items: [entry], seconds_to_reset: null },
    });
    // Registration is already complete. The modal closes itself after this
    // callback; the full board refresh must never turn a successful POST into a
    // misleading registration error.
    syncRegisteredBoard(entry, registeredKey, registeredMode, true);
  }

  const footerDisabled =
    !!validationError ||
    symbolSearchBusy ||
    (screen.kind === "chart" && !chartReady) ||
    (screen.kind === "backtest" && backtest.currentBusy) ||
    (screen.kind === "paper" && (!paperReady || paperController.busy)) ||
    (screen.kind === "register" && !registrationReady);
  const stageIsOutput = ["chart", "backtest", "paper"].includes(screen.kind) || (screen.kind === "register" && !registrationReady);
  const stageIsShowcase = screen.chapter === 4 || ["community", "news", "guide"].includes(screen.kind) || (screen.kind === "register" && registrationReady);
  const showWorkspaceContext = ["strategy", "condition", "risk", "period", "backtest", "paper", "register"].includes(screen.kind) && !registrationReady;

  return (
    <div className="hero-tour">
      <section className="hero-progress-band" aria-label="시작 가이드 진행률">
        <div className="hero-progress-meta">
          <span className="t-caption text-slate-700 num">{String(screen.chapter).padStart(2, "0")} / {String(CHAPTERS.length).padStart(2, "0")}</span>
          <span className="t-caption text-slate-500">{chapterName}</span>
        </div>
        <div
          className="hero-tour-progress-track"
          role="progressbar"
          aria-valuemin="1"
          aria-valuemax={CHAPTERS.length}
          aria-valuenow={screen.chapter}
          aria-valuetext={`${chapterName}, ${CHAPTERS.length}단계 중 ${screen.chapter}단계`}
        >
          <span style={{ width: `${progress}%` }} />
        </div>
        <ol
          className="hero-progress-chapters"
          aria-hidden="true"
          style={{ gridTemplateColumns: `repeat(${CHAPTERS.length}, minmax(0, 1fr))` }}
        >
          {CHAPTERS.map((chapter, index) => (
            <li key={chapter} className={index + 1 === screen.chapter ? "is-current" : index + 1 < screen.chapter ? "is-done" : ""}>
              {chapter}
            </li>
          ))}
        </ol>
      </section>

      <section
        key={screen.chapter === 4 ? "condition-workbench" : screen.id}
        className={
          "hero-tour-stage hero-stage-enter " +
          (stageIsOutput ? "hero-stage-output " : "") +
          (screen.kind === "register" ? "hero-stage-register " : "") +
          (stageIsShowcase ? "hero-stage-showcase" : "")
        }
        aria-labelledby="hero-stage-title"
      >
        <div className="hero-tour-copy">
          <div className="t-caption text-slate-500">
            {copy.eyebrow}
            {screen.substep ? <span className="ml-2 num">조건 {screen.substep} / {screen.substepTotal}</span> : null}
          </div>
          <h1 id="hero-stage-title" ref={headingRef} tabIndex={-1} className="mt-3 t-display text-slate-900">
            <HeroTitle value={copy.title} />
          </h1>
          <p className="mt-5 t-body text-slate-600 measure">{copy.description}</p>
          {screen.chapter === 4 ? (
            <p className="mt-5 t-caption text-slate-500">
              빠른 시작에 필요한 대표 조건만 정하고 있어요. A–K의 모든 위험·비용·데이터 설정은 전체 빌더에 그대로 있어요.
            </p>
          ) : null}
        </div>
        <div ref={workRef} className="hero-tour-work">
          <div className="hero-workspace">
            <header className="hero-workspace-bar">
              <span className="hero-workspace-title">{workspaceLabel(screen)}</span>
              {workspaceStatus(screen) ? <span className="hero-workspace-status">{workspaceStatus(screen)}</span> : null}
            </header>
            {showWorkspaceContext ? (
              <dl className="hero-workspace-context" aria-label="현재 가이드 설정">
                <div><dt>종목</dt><dd className="num">{macro.symbol}</dd></div>
                <div><dt>전략</dt><dd className="hero-workspace-strategy">{RULE_TYPES[macro.rule_type]?.label || macro.rule_type}</dd></div>
                <div><dt>손실 제한</dt><dd className="num">{macro.risk?.stop_loss_pct == null ? "사용 안 함" : `${macro.risk.stop_loss_pct}%`}</dd></div>
                <div><dt>기간</dt><dd>{periodSummary(form)}</dd></div>
              </dl>
            ) : null}
            <div className="hero-workspace-body">
              {resumePhase === "restoring" ? (
                <div className="notice mb-5 t-small text-slate-700" role="status">로그인 전에 고른 설정으로 백테스트를 다시 확인하고 있어요.</div>
              ) : null}
              {resumeError ? <div className="mb-5 t-small text-red-600" role="alert">{resumeError}</div> : null}
              <HeroScene
                screen={screen}
                form={form}
                setForm={setForm}
                macro={macro}
                validationError={validationError}
                backtest={backtest}
                paperController={paperController}
                registrationEligible={registrationEligible}
                registeredEntry={registeredEntry}
                registeredBoard={registeredBoard}
                boardLoading={boardLoading}
                boardError={boardError}
                onOpenRegister={openRegister}
                chartLoadState={{
                  searchBusy: symbolSearchBusy,
                  searchError: symbolSearchError,
                  onLoadState: handleChartLoadState,
                }}
              />
            </div>
          </div>
        </div>
      </section>

      <footer className="hero-tour-footer">
        <div className="hero-tour-footer-inner">
          <div>
            {previousScreen ? (
              <button
                type="button"
                onClick={goPrevious}
                disabled={paperController.busy || backtest.busy || resumePhase === "restoring"}
                className="btn btn-l btn-ghost"
              >
                이전 화면
              </button>
            ) : (
              <Link to="/builder?guide=1&from=hero" state={builderState} className="btn btn-l btn-ghost">
                바로 만들기
              </Link>
            )}
          </div>
          <div className="min-w-0 flex-1 text-right">
            {screen.kind === "register" && !registrationReady ? (
              <p className="hero-tour-action-note" role="status">위에서 실제 등록을 완료하면 다음 안내가 열려요.</p>
            ) : (
              <button type="button" onClick={advance} disabled={footerDisabled} className="btn btn-l btn-primary hero-tour-next">
                {actionLabel(screen, backtest, paperController, paperReady, registrationReady, symbolSearchBusy, chartLoad.status)}
              </button>
            )}
          </div>
        </div>
      </footer>

      {registerOpen && registrationEligible && backtest.testedKey === currentKey && backtest.testedMacro ? (
        <Suspense fallback={(
          <div
            className="scrim fixed inset-0 z-50 grid place-items-center p-4"
            role="dialog"
            aria-modal="true"
            aria-label="등록 화면 불러오는 중"
            tabIndex={-1}
            autoFocus
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              event.preventDefault();
              setRegistrationLayer(false);
            }}
          >
            <div className="dialog px-6 py-5 t-small text-slate-700" role="status">등록 화면 불러오는 중…</div>
          </div>
        )}>
          <RegisterMacroModal
            key={`hero-register-${backtest.testedKey}-${registrationMode}`}
            open
            reviewOnly
            initialMacro={backtest.testedMacro}
            initialMode={registrationMode}
            draftOrigin="hero"
            loginReturnPath="/?guide=1&tour=register&resume=hero-register"
            onClose={() => setRegistrationLayer(false)}
            onDone={finishRegistration}
          />
        </Suspense>
      ) : null}
    </div>
  );
}
