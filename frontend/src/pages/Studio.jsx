import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Builder from "../components/Builder.jsx";
import ResultView from "../components/ResultView.jsx";
import SimBadge from "../components/SimBadge.jsx";
import PaperPanel from "../components/PaperPanel.jsx";
import CandleChart from "../components/CandleChart.jsx";
import OptimizePanel from "../components/OptimizePanel.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import ProductTour from "../components/ProductTour.jsx";
import { PageHeader } from "../components/Page.jsx";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import {
  RULE_TYPES,
  buildMacro,
  defaultForm,
  macroToForm,
  validate,
  withTypeDefaults,
} from "../lib/macro.js";
import { computeStrategyOverlay } from "../lib/indicators.js";
import {
  completeJourney,
  peekRegistrationDraft,
  readHeroDraft,
  takeRegistrationDraft,
} from "../lib/journey.js";

const MAX_MACRO_FILE_BYTES = 2 * 1024 * 1024;

// '사용법 안내' 프로덕트 투어 단계. 각 anchor 는 화면의 data-tour 요소를 가리킨다.
const TOUR_STEPS = [
  {
    anchor: "market-briefing",
    title: "시장 브리핑",
    body: "먼저 시장 분위기를 확인해요. ‘시장 브리핑 보기’를 누르면 김치 프리미엄(국내외 가격 차이 · +김프/−역프)과 공포·탐욕 지수(0~100, 시장 심리)를 볼 수 있어요. 매매 전 참고용 지표예요.",
  },
  {
    anchor: "symbol",
    title: "종목 입력",
    body: "확인할 코인을 정해요. 예: BTCUSDT. 여러 종목은 쉼표로 나눠 쓰면 자금을 종목 수만큼 균등하게 나눠 확인해요.",
  },
  {
    anchor: "strategy",
    title: "매매 방식 선택",
    body: "이동평균 크로스·볼린저·RSI 등 원하는 전략을 골라요. 라벨 옆 ⓘ에 마우스를 올리면 각 매매 방식이 어떤 규칙인지 설명을 확인할 수 있어요.",
  },
  {
    anchor: "position",
    title: "포지션",
    body: "오를 때 버는 롱(long), 내릴 때 버는 숏(short)을 정해요. 전략에 따라 숏이 막혀 있을 수 있어요.",
  },
  {
    anchor: "interval",
    title: "봉 간격",
    body: "지표 계산과 체결을 판정하는 캔들 단위예요. 1분·1시간·1일처럼 전략에 맞는 시간 단위를 골라요.",
  },
  {
    anchor: "period",
    title: "테스트 기간",
    body: "과거 어느 구간의 데이터로 확인할지 정해요. 최근 1년·6개월·3개월 또는 직접 기간을 지정할 수 있어요.",
  },
  {
    anchor: "chart",
    title: "실시간 차트 · 보조지표",
    body: "지금 고른 종목의 실시간 시세를 보여줘요. 선택한 매매 방식의 보조지표(예: 이동평균·볼린저 밴드)가 함께 그려지고, 아래 설정값을 바꾸면 보조지표도 즉시 따라 바뀌는 걸 확인할 수 있어요.",
  },
  {
    anchor: "strategy-params",
    title: "전략 조건",
    body: "고른 매매 방식에만 필요한 세부 값을 정해요. 익절 기준·이동평균 기간·밴드 폭처럼 전략마다 항목이 달라져요.",
  },
  {
    anchor: "risk",
    title: "손실 제한",
    body: "한 번에 쓸 자금 비율과 손절 기준(%)을 정해요. 손절을 켜면 정해진 손실에서 자동으로 정리해 위험을 제한해요.",
  },
  {
    anchor: "advanced-risk",
    title: "고급 위험 관리",
    body: "하루 최대 손실·최대 보유 시간·손절 뒤 쉬는 시간 같은 추가 안전장치예요. 필요할 때만 설정하면 돼요.",
  },
  {
    anchor: "fees",
    title: "거래 비용과 펀딩비",
    body: "실제에 가깝게 수수료·체결 가격 차이(슬리피지)·펀딩비를 반영해요. ‘실제 펀딩비 가져오기’로 해당 기간 평균값을 자동으로 채울 수 있어요.",
  },
  {
    anchor: "leverage",
    title: "레버리지",
    body: "배수를 올리면 수익도 손실도 그만큼 커지고 청산 위험이 생겨요. 1배는 현물과 같아 청산이 없어요. 백테스트·모의에서만 적용돼요.",
  },
];

function macroKey(macro) {
  return JSON.stringify(macro);
}

function periodLabelOf(macro) {
  if (macro.period?.preset === "custom") {
    return `${macro.period.start || "?"} ~ ${macro.period.end || "?"}`;
  }
  return { "1y": "최근 1년", "6m": "최근 6개월", "3m": "최근 3개월" }[
    macro.period?.preset
  ] || macro.period?.preset || "";
}

// 조건 정하기 맨 위 블록 — 차트를 정하는 설정(children)과 그 차트를 한 덩어리로
// 묶어 스크롤에 고정한다. 설정은 늘 보이고, 차트만 접을 수 있다.
function ChartDisclosure({ symbols, form, setForm, children }) {
  const [open, setOpen] = useState(true);
  const contentId = useId();

  // 실시간 차트에 지금 매크로 설정 그대로 보조지표를 얹는다(예: 볼린저 상·하단
  // 밴드와 매수·매도 구간). form 이 바뀌면 오버레이도 즉시 따라간다.
  const overlay = useCallback((candles) => computeStrategyOverlay(form, candles), [form]);
  const ruleLabel = RULE_TYPES[form.rule_type]?.label || "";

  return (
    // 설정과 차트는 맨 위에 나란히 놓되 스티키는 차트에만 건다. 둘을 한 상자로
    // 묶으면 700px 이 넘어 화면의 8할을 먹는다 — 설정은 한 번 정하면 끝이라
    // 흘려보내고, 스크롤을 내리면 차트만 위에 남는다. 폼이 밑으로 비쳐 보이지
    // 않도록 캔버스 색을 깔아 둔다.
    <>
      {children}

      {symbols.length > 0 ? (
        <section className="builder-chart-sticky border-t border-slate-200" data-tour="chart">
          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            className="w-full py-2 flex items-center justify-between gap-3 text-left"
            aria-expanded={open}
            aria-controls={contentId}
          >
            <span className="min-w-0">
              <span className="t-label text-slate-900">실시간 차트 · 보조지표</span>
              <span className="ml-2 t-caption text-slate-500 num">
                {symbols.length === 1 ? symbols[0] : `${symbols.length}개 종목`}
              </span>
              {/* 설명 문단이던 것을 헤더 한 줄로 접었다 — 스티키로 계속 붙어 있는
                  영역이라 두 줄짜리 안내가 매번 자리를 차지할 이유가 없다. */}
              {ruleLabel ? <span className="ml-2 t-caption text-slate-500">· {ruleLabel}</span> : null}
            </span>
            <span className="t-caption text-slate-400" aria-hidden="true">{open ? "접기 ↑" : "펼치기 ↓"}</span>
          </button>
          {open ? (
            <div id={contentId} className="pb-3 space-y-5">
              {/* 시장은 넘기지 않는다(= 현물). 보조지표는 rule_type·포지션·전략 조건만
                  보고 그려져 레버리지와 무관한데, 레버리지로 선물 캔들을 끌어오면
                  지표는 그대로인 채 배포 리전의 선물 차단에 걸려 차트만 사라졌다.

                  interval 은 controlled — 위의 '봉 간격'과 차트 툴바가 같은 값을
                  가리켜야 한다. defaultInterval 로 두면 툴바에서 바꾼 간격이
                  매크로에 반영되지 않아 둘이 조용히 어긋난다.

                  minimal — 조작 안내·면책 문단을 접어 스티키 높이를 줄인다. 확대·축소
                  버튼은 그대로 남는다(compact 와 달리 minimal 은 컨트롤을 지우지 않는다). */}
              {symbols.map((symbol) => (
                <CandleChart
                  key={symbol}
                  symbol={symbol}
                  interval={form.candle_interval || "1m"}
                  onIntervalChange={(value) => setForm((f) => ({ ...f, candle_interval: value }))}
                  overlay={overlay}
                  minimal
                />
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </>
  );
}

export default function Studio() {
  const { token } = useAuth();
  const { slug } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const entryQuery = searchParams.toString();
  const [form, setForm] = useState(defaultForm);
  const [result, setResult] = useState(null);
  const [testedMacro, setTestedMacro] = useState(null);
  const [perSymbol, setPerSymbol] = useState([]);
  const [explanation, setExplanation] = useState(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [summary, setSummary] = useState("");
  const [dataSource, setDataSource] = useState("");
  const [periodLabel, setPeriodLabel] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [share, setShare] = useState(null);
  const [loadedFrom, setLoadedFrom] = useState("");
  const [runLeverage, setRunLeverage] = useState(1);
  const [autoRun, setAutoRun] = useState(true);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registrationMode, setRegistrationMode] = useState("live");
  const [hasRegistrationDraft, setHasRegistrationDraft] = useState(
    () => {
      const draft = peekRegistrationDraft();
      return !!draft && draft.context?.origin !== "hero";
    }
  );
  const [tourOpen, setTourOpen] = useState(false);
  const [builderTab, setBuilderTab] = useState("basic"); // "basic" | "pro" (프로는 개발 중)
  const [fileImportBusy, setFileImportBusy] = useState(false);
  const [fileImportError, setFileImportError] = useState("");
  const [fileImportSuccess, setFileImportSuccess] = useState("");
  const macroFileInputRef = useRef(null);
  const requestIdRef = useRef(0);
  const lastAttemptKeyRef = useRef("");
  const aiRequestIdRef = useRef(0);
  const latestTestedKeyRef = useRef("");
  const resumeRequestIdRef = useRef(0);
  const processedEntryQueryRef = useRef("");

  const valErr = validate(form);
  const currentMacro = useMemo(() => buildMacro(form), [form]);
  const currentMacroKey = useMemo(() => macroKey(currentMacro), [currentMacro]);
  const testedMacroKey = testedMacro ? macroKey(testedMacro) : "";
  latestTestedKeyRef.current = testedMacroKey;
  const resultIsFresh = !!testedMacro && testedMacroKey === currentMacroKey;

  const chartSymbols = useMemo(() => {
    const seen = new Set();
    const out = [];
    for (const part of (form.symbol || "").split(",")) {
      const symbol = part.trim().toUpperCase();
      if (!symbol || seen.has(symbol)) continue;
      seen.add(symbol);
      out.push(symbol);
      if (out.length === 5) break;
    }
    return out;
  }, [form.symbol]);

  // Clone flow: load a shared macro into the builder.
  useEffect(() => {
    if (!slug) return;
    let alive = true;
    setBusy(true);
    api
      .getMacro(slug)
      .then((data) => {
        if (!alive) return;
        setForm(macroToForm(data.macro));
        setLoadedFrom(data.human_summary);
        setShare({
          slug,
          url: `${window.location.origin}/s/${slug}`,
          macroKey: macroKey(buildMacro(macroToForm(data.macro))),
        });
      })
      .catch((reason) => alive && setError(String(reason.message || reason)))
      .finally(() => alive && setBusy(false));
    return () => {
      alive = false;
    };
  }, [slug]);

  // Copy-to-builder from a leaderboard entry. Consume only router user state;
  // preserve React Router's own key/index bookkeeping.
  useEffect(() => {
    const macro = location.state?.macro;
    if (!macro) return;
    setForm(macroToForm(macro));
    setLoadedFrom(
      location.state?.source === "hero-guide"
        ? "시작 가이드에서 고른 설정"
        : "리더보드에서 복사한 매크로"
    );
    navigate(location.pathname + location.search, { replace: true, state: null });
  }, [location.pathname, location.search, location.state, navigate]);

  const runBacktest = useCallback(async (snapshot) => {
    const validationError = validate(snapshot);
    setError("");
    if (validationError) {
      setError(validationError);
      return false;
    }

    const macro = buildMacro(snapshot);
    const key = macroKey(macro);
    const requestId = ++requestIdRef.current;
    // Record attempts, not only successes. An automatic run that fails should
    // wait for an edit or an explicit retry instead of hammering the API every
    // 700ms with the same invalid/unavailable input.
    lastAttemptKeyRef.current = key;
    aiRequestIdRef.current += 1;
    setAiBusy(false);
    setBusy(true);
    try {
      const data = await api.backtest(macro);
      if (requestId !== requestIdRef.current) return false;
      setTestedMacro(macro);
      setResult(data.result);
      setPerSymbol(data.per_symbol || []);
      setExplanation(data.explanation || null);
      setAiError("");
      setSummary(data.human_summary);
      setDataSource(data.data_source);
      setPeriodLabel(data.period_label);
      setRunLeverage(macro.leverage || 1);
      return true;
    } catch (reason) {
      if (requestId === requestIdRef.current) setError(String(reason.message || reason));
      return false;
    } finally {
      if (requestId === requestIdRef.current) setBusy(false);
    }
  }, []);

  // Consume all entry parameters in one place so two effects cannot restore
  // each other's deleted query keys. A login-return draft wins over starter
  // presets and is re-tested on the server before registration reopens.
  useEffect(() => {
    if (slug) return;
    const entryParams = new URLSearchParams(entryQuery);
    const symbol = entryParams.get("symbol");
    const strategy = entryParams.get("strategy");
    const validStrategy = strategy && RULE_TYPES[strategy] ? strategy : "";
    const resumeRegistration = entryParams.get("register") === "1";
    const fromHero = entryParams.get("from") === "hero";
    if (!symbol && !validStrategy && !resumeRegistration && !fromHero) {
      processedEntryQueryRef.current = "";
      return;
    }
    if (processedEntryQueryRef.current === entryQuery) return;
    processedEntryQueryRef.current = entryQuery;
    const resumeRequestId = ++resumeRequestIdRef.current;

    const nextParams = new URLSearchParams(entryParams);
    nextParams.delete("symbol");
    nextParams.delete("strategy");
    nextParams.delete("register");
    nextParams.delete("from");
    setSearchParams(nextParams, { replace: true });

    if (resumeRegistration) {
      const pendingDraft = peekRegistrationDraft();
      if (pendingDraft?.context?.origin === "hero") {
        setError("시작 가이드에서 보관한 등록 설정이에요. 시작 화면에서 등록을 이어가 주세요.");
        setHasRegistrationDraft(false);
        return;
      }
      const draft = takeRegistrationDraft();
      setHasRegistrationDraft(false);
      if (!draft?.macro) {
        setError("이어갈 등록 설정을 찾지 못했어요. 조건을 확인하고 백테스트를 다시 실행해 주세요.");
        return;
      }

      let restored;
      try {
        restored = macroToForm(draft.macro);
      } catch (_) {
        setError("보관한 등록 설정을 읽지 못했어요. 조건을 다시 정해 주세요.");
        return;
      }

      setForm(restored);
      setRegistrationMode(draft.context?.mode === "replay" ? "replay" : "live");
      setLoadedFrom("로그인 완료 · 등록 전 결과를 다시 확인하는 중");
      runBacktest(restored).then((ok) => {
        if (resumeRequestId !== resumeRequestIdRef.current || !ok) return;
        setLoadedFrom("로그인 완료 · 같은 설정으로 백테스트를 다시 확인했어요");
        setRegisterOpen(true);
      });
      return;
    }

    if (fromHero) {
      const heroMacro = readHeroDraft();
      if (!heroMacro) {
        setError("시작 가이드에서 고른 설정을 찾지 못했어요. 조건을 다시 확인해 주세요.");
        return;
      }
      try {
        setForm(macroToForm(heroMacro));
        setLoadedFrom("시작 가이드에서 고른 설정");
      } catch (_) {
        setError("시작 가이드 설정을 읽지 못했어요. 조건을 다시 정해 주세요.");
      }
      return;
    }

    setForm((previous) => {
      let next = previous;
      if (validStrategy) next = withTypeDefaults(next, validStrategy);
      if (symbol) next = { ...next, symbol: symbol.toUpperCase() };
      return next;
    });
  }, [entryQuery, runBacktest, setSearchParams, slug]);

  // Once the first result exists, re-run after edits settle. Freshness keys and
  // request IDs prevent stale responses from becoming registerable results.
  useEffect(() => {
    if (!autoRun || !testedMacro || valErr || busy || registerOpen) return;
    if (currentMacroKey === lastAttemptKeyRef.current) return;
    const snapshot = form;
    const timer = window.setTimeout(() => runBacktest(snapshot), 700);
    return () => window.clearTimeout(timer);
  }, [autoRun, busy, currentMacroKey, form, registerOpen, runBacktest, testedMacro, valErr]);

  useEffect(() => {
    const onKeyDown = (event) => {
      if (registerOpen || !(event.ctrlKey || event.metaKey) || event.key !== "Enter") return;
      event.preventDefault();
      if (!valErr && !busy) runBacktest(form);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [busy, form, registerOpen, runBacktest, valErr]);

  async function saveAndShare() {
    setError("");
    if (valErr) return setError(valErr);
    const requestId = ++requestIdRef.current;
    const key = currentMacroKey;
    lastAttemptKeyRef.current = key;
    aiRequestIdRef.current += 1;
    setAiBusy(false);
    setBusy(true);
    try {
      const macro = currentMacro;
      const data = await api.createMacro(macro);
      if (requestId !== requestIdRef.current) return;
      setTestedMacro(macro);
      setResult(data.result);
      // The save endpoint returns an aggregate representative result but no
      // per-symbol breakdown. Clear previous rows instead of attaching them to
      // the newly saved result, and derive the period label from the snapshot.
      setPerSymbol((previous) => (testedMacroKey === key ? previous : []));
      setExplanation(data.explanation || null);
      setAiError("");
      setSummary(data.human_summary);
      setDataSource(data.data_source);
      setPeriodLabel(periodLabelOf(macro));
      setRunLeverage(macro.leverage || 1);
      setShare({
        slug: data.share_slug,
        url: `${window.location.origin}/s/${data.share_slug}`,
        macroKey: key,
      });
    } catch (reason) {
      if (requestId === requestIdRef.current) setError(String(reason.message || reason));
    } finally {
      if (requestId === requestIdRef.current) setBusy(false);
    }
  }

  async function registerMacroFile(file) {
    setFileImportError("");
    setFileImportSuccess("");
    if (!file) return;
    if (!token) {
      setFileImportError("내 매크로에 등록하려면 먼저 로그인해 주세요.");
      return;
    }
    if (file.size > MAX_MACRO_FILE_BYTES) {
      setFileImportError("2MB 이하의 껄무새 매크로 파일을 선택해 주세요.");
      return;
    }

    setFileImportBusy(true);
    try {
      const rawMacro = JSON.parse(await file.text());
      if (!rawMacro || typeof rawMacro !== "object" || !rawMacro.symbol || !rawMacro.rule_type || !rawMacro.params) {
        throw new Error("INVALID_MACRO_FILE");
      }
      const importedForm = macroToForm(rawMacro);
      const validationError = validate(importedForm);
      if (validationError) throw new Error(validationError);

      const macro = buildMacro(importedForm);
      const name = file.name.replace(/\.ggm\.json$|\.json$/i, "") || `${macro.symbol} 매크로`;
      const data = await api.saveMyMacro(macro, name);
      const savedMacro = data?.item?.macro || macro;

      requestIdRef.current += 1;
      aiRequestIdRef.current += 1;
      lastAttemptKeyRef.current = "";
      setForm(macroToForm(savedMacro));
      setTestedMacro(null);
      setResult(null);
      setPerSymbol([]);
      setExplanation(null);
      setAiBusy(false);
      setAiError("");
      setSummary("");
      setDataSource("");
      setPeriodLabel("");
      setShare(null);
      setLoadedFrom(`내 매크로 등록 완료 · ${data?.item?.name || name}`);
      setError("");
      setFileImportSuccess("내 매크로에 등록하고 아래 조건 편집기에 불러왔어요. 백테스트로 설정을 다시 확인해 주세요.");
    } catch (reason) {
      const message = String(reason.message || reason);
      setFileImportError(
        message === "INVALID_MACRO_FILE" || reason instanceof SyntaxError
          ? "껄무새에서 받은 .ggm.json 파일인지 확인해 주세요."
          : reason?.status === 401
            ? "로그인이 만료됐어요. 다시 로그인한 뒤 등록해 주세요."
            : `매크로 파일을 등록하지 못했어요: ${message}`,
      );
    } finally {
      setFileImportBusy(false);
    }
  }

  function onMacroFileChange(event) {
    const file = event.target.files?.[0];
    void registerMacroFile(file);
    event.target.value = "";
  }

  async function enrichExplanation() {
    if (aiBusy || !result || !testedMacro) return;
    const macro = testedMacro;
    const key = macroKey(macro);
    const requestId = ++aiRequestIdRef.current;
    setAiBusy(true);
    setAiError("");
    try {
      const data = await api.explainAi(macro);
      if (requestId !== aiRequestIdRef.current || latestTestedKeyRef.current !== key) return;
      if (data.explanation) setExplanation(data.explanation);
      if (data.ai_available === false) setAiError("AI 해설이 아직 준비되지 않았어요 (서버 설정 필요).");
      else if (data.ai_error) setAiError(data.ai_error);
    } catch (reason) {
      if (requestId === aiRequestIdRef.current && latestTestedKeyRef.current === key) {
        setAiError("AI 호출 실패: " + String(reason.message || reason));
      }
    } finally {
      if (requestId === aiRequestIdRef.current) setAiBusy(false);
    }
  }

  function finishRegistration(entry) {
    completeJourney();
    setRegisterOpen(false);
    navigate("/leaderboard", {
      replace: true,
      state: { justRegistered: true, registeredId: entry?.id || null },
    });
  }

  function openRegistration(mode = "live") {
    setRegistrationMode(mode === "replay" ? "replay" : "live");
    setRegisterOpen(true);
  }

  return (
    <div>
      <PageHeader
        eyebrow="조건 정하기 → 결과 확인 → 리더보드 등록"
        title="매크로 만들기"
        description="종목과 거래 조건을 정하고, 과거 데이터로 확인한 뒤 같은 설정을 리더보드에 등록할 수 있어요."
        actions={<SimBadge />}
      />

      {/* 유틸리티 한 줄 — 파일 등록과 사용법 안내는 본문이 아니라 부속 동작이라
          제목·설명 문단으로 두 블록을 차지할 이유가 없다. 괘선도 두지 않는다:
          구획을 나눌 만한 내용이 아니라 버튼 두 개뿐이다. */}
      <section className="mb-6" aria-label="빌더 도구">
        <input
          ref={macroFileInputRef}
          type="file"
          accept=".json,.ggm.json,application/json"
          onChange={onMacroFileChange}
          className="sr-only"
          aria-label="껄무새 매크로 파일 등록"
        />
        <div className="py-1 flex items-center gap-2 flex-wrap">
          {!slug ? (
            token ? (
              <button
                type="button"
                onClick={() => macroFileInputRef.current?.click()}
                disabled={fileImportBusy || busy}
                className="btn btn-s btn-ghost"
                title="가지고 있는 .ggm.json 매크로 파일을 내 매크로에 등록하고 아래에서 이어서 수정해요"
              >
                {fileImportBusy ? "파일 등록 중…" : "매크로 파일 등록"}
              </button>
            ) : (
              <Link
                to="/login?next=%2Fbuilder"
                className="btn btn-s btn-ghost"
                title="가지고 있는 매크로 파일을 등록하려면 로그인이 필요해요"
              >
                로그인 후 파일 등록
              </Link>
            )
          ) : null}
          <button
            type="button"
            onClick={() => {
              setBuilderTab("basic"); // 투어가 짚는 요소는 모두 기본 빌더 안에 있다.
              setTourOpen(true);
            }}
            className="btn btn-s btn-ghost"
            title="화면을 순서대로 짚어가며 사용법을 안내해 드려요"
          >
            사용법 안내
          </button>
        </div>
        {fileImportError ? <p className="mt-2 t-small text-red-600" role="alert">{fileImportError}</p> : null}
        {fileImportSuccess ? <p className="mt-2 t-small text-green-700" role="status">{fileImportSuccess}</p> : null}
      </section>

      {hasRegistrationDraft ? (
        <div className="notice mb-7 t-small text-slate-700 flex items-center justify-between gap-4 flex-wrap">
          <span>로그인 화면으로 가기 전에 테스트한 등록 설정이 남아 있어요.</span>
          <button
            type="button"
            onClick={() => navigate("/builder?guide=1&register=1")}
            className="btn btn-m btn-secondary"
          >
            등록 계속하기
          </button>
        </div>
      ) : null}

      <div className="grid lg:grid-cols-2 gap-8 lg:gap-10">
        <section id="macro-editor">
          <div className="mb-5">
            <div className="t-caption text-slate-500 num">01</div>
            <h2 className="mt-1 t-h4 text-slate-900">조건 정하기</h2>
            <p className="mt-2 t-small text-slate-700">처음에는 종목과 전략만 바꾸고 나머지는 기본값으로 확인해도 돼요.</p>
          </div>

          <div className="seg mb-5 max-w-full flex-wrap" role="tablist" aria-label="매크로 빌더 종류">
            <button
              type="button"
              role="tab"
              id="builder-tab-basic"
              aria-selected={builderTab === "basic"}
              aria-controls="builder-panel-basic"
              onClick={() => setBuilderTab("basic")}
              className={"seg-item " + (builderTab === "basic" ? "seg-item-on" : "")}
            >
              기본 매크로 빌더
            </button>
            <button
              type="button"
              role="tab"
              id="builder-tab-pro"
              aria-selected={builderTab === "pro"}
              aria-controls="builder-panel-pro"
              onClick={() => setBuilderTab("pro")}
              className={"seg-item " + (builderTab === "pro" ? "seg-item-on" : "")}
            >
              프로 매크로 빌더
              <span className="badge badge-flat ml-2">개발 예정</span>
            </button>
          </div>

          {loadedFrom ? (
            <div className="notice mb-5 t-small text-slate-700">
              불러온 매크로 · <b className="text-slate-900">{loadedFrom}</b>
            </div>
          ) : null}

          {/* 프로 빌더는 아직 준비 중이라 폼을 열지 않는다. 기본 빌더는 계속 마운트해
              두어 탭을 오가도 입력한 조건이 사라지지 않게 한다. */}
          <div
            id="builder-panel-basic"
            role="tabpanel"
            aria-labelledby="builder-tab-basic"
            hidden={builderTab !== "basic"}
          >
            <Builder
              form={form}
              setForm={setForm}
              chartSlot={(basicSettings) => (
                <ChartDisclosure symbols={chartSymbols} form={form} setForm={setForm}>
                  {basicSettings}
                </ChartDisclosure>
              )}
            />
          </div>

          {builderTab === "pro" ? (
            <div
              id="builder-panel-pro"
              role="tabpanel"
              aria-labelledby="builder-tab-pro"
              className="py-14 text-center border-b border-slate-200"
            >
              <div className="t-title text-slate-900">프로 매크로 빌더는 준비 중이에요</div>
              <p className="mt-2 t-small text-slate-700 measure mx-auto">
                여러 조건을 조합하고 진입·청산 규칙을 직접 짜는 고급 빌더예요. 아직 개발 중이라
                지금은 사용할 수 없어요. 그동안은 기본 매크로 빌더로 조건을 만들어 주세요.
              </p>
              <button
                type="button"
                onClick={() => setBuilderTab("basic")}
                className="btn btn-m btn-secondary mt-5"
              >
                기본 매크로 빌더로 돌아가기
              </button>
            </div>
          ) : null}

          {builderTab === "basic" ? (
            <>
              {valErr ? <div className="mt-4 t-small text-amber-700" role="alert">{valErr}</div> : null}
              {error ? <div className="mt-4 t-small text-red-600" role="alert">오류: {error}</div> : null}
              <div className="mt-6 flex items-center gap-3 flex-wrap">
                <button onClick={() => runBacktest(form)} disabled={busy || !!valErr}
                  className={"btn btn-l " + (resultIsFresh ? "btn-secondary" : "btn-primary")}>
                  {busy ? "결과 계산 중…" : testedMacro && !resultIsFresh ? "바뀐 조건으로 다시 테스트" : "이 조건으로 백테스트"}
                </button>
                <button onClick={saveAndShare} disabled={busy || !!valErr} className="btn btn-l btn-secondary">
                  저장하고 공유 링크 만들기
                </button>
                <label className="flex items-center gap-2 t-small text-slate-700 cursor-pointer select-none">
                  <input type="checkbox" checked={autoRun} onChange={(event) => setAutoRun(event.target.checked)} />
                  변경 뒤 자동 테스트
                </label>
              </div>
              <p className="mt-3 t-caption text-slate-500">
                첫 결과 뒤부터 자동 테스트가 동작해요 · <kbd className="num rounded border border-slate-300 bg-slate-100 px-1">Ctrl</kbd>+<kbd className="num rounded border border-slate-300 bg-slate-100 px-1">Enter</kbd>
              </p>
            </>
          ) : null}
        </section>

        <section id="backtest-result">
          <div className="mb-5">
            <div className="t-caption text-slate-500 num">02</div>
            <h2 className="mt-1 t-h4 text-slate-900">결과 확인</h2>
            <p className="mt-2 t-small text-slate-700">수익률과 함께 최대낙폭, 홀딩 비교, 청산 위험을 확인해요.</p>
          </div>

          <div className="sr-only" role="status" aria-live="polite">
            {busy ? "백테스트 결과를 계산하고 있어요." : resultIsFresh ? "백테스트 결과가 준비됐어요." : ""}
          </div>

          {!result && !busy ? (
            <div className="py-14 text-center border-b border-slate-200">
              <div className="t-title text-slate-900">아직 계산한 결과가 없어요</div>
              <div className="mt-2 t-small text-slate-700">왼쪽의 백테스트 버튼을 누르면 이곳에 결과가 나와요.</div>
            </div>
          ) : null}

          {result && !resultIsFresh ? (
            <div className="notice-warn my-5 t-small text-slate-700">
              결과를 낸 뒤 조건이 바뀌었어요. 아래 숫자는 이전 조건의 결과이며 등록에는 사용할 수 없어요.
            </div>
          ) : null}

          {result ? (
            <div className="mt-6">
              <ResultView
                result={result}
                perSymbol={perSymbol}
                explanation={explanation}
                onAiExplain={enrichExplanation}
                aiBusy={aiBusy}
                aiError={aiError}
                summary={summary}
                dataSource={dataSource}
                periodLabel={periodLabel}
                symbol={testedMacro?.symbol || form.symbol}
                leverage={runLeverage}
              />
            </div>
          ) : null}

          {resultIsFresh ? (
            <section className="mt-6 py-5 border-y border-slate-200 flex items-center justify-between gap-4 flex-wrap">
              <div>
                <div className="t-caption text-slate-500 num">03</div>
                <h3 className="mt-1 t-title text-slate-900">리더보드 등록</h3>
                <p className="mt-1 t-small text-slate-700">테스트한 설정을 바꾸지 않고 모의매매에 사용해요.</p>
              </div>
              <button onClick={() => openRegistration()} className="btn btn-l btn-primary">
                이 설정으로 등록
              </button>
            </section>
          ) : null}

          {result && form.rule_type === "A" ? (
            <div className="mt-6">
              <OptimizePanel form={form} setForm={setForm} valErr={valErr} />
            </div>
          ) : null}

          {result ? (
            <div className="mt-6">
              <PaperPanel macro={currentMacro} valErr={valErr} onRegister={resultIsFresh ? openRegistration : null} />
            </div>
          ) : null}

          {share ? (
            <section className="mt-6 pt-5 border-t border-slate-200 space-y-4">
              <h3 className="t-title text-slate-900">저장·공유</h3>
              {share.macroKey && share.macroKey !== currentMacroKey ? (
                <div className="notice-warn t-small text-slate-700">
                  이 링크는 저장 당시 설정을 가리켜요. 지금 바꾼 조건을 공유하려면 새 링크를 만들어 주세요.
                </div>
              ) : null}
              <div className="flex gap-2">
                <input readOnly value={share.url} aria-label="공유 링크" className="field field-sm flex-1" />
                <button onClick={() => navigator.clipboard?.writeText(share.url)} className="btn btn-m btn-secondary">
                  링크 복사
                </button>
              </div>
              <img src={api.cardUrl(share.slug)} alt="공유용 백테스트 인증 카드" className="w-full rounded-xl border border-slate-200" />
              <a href={api.cardUrl(share.slug)} download={`${share.slug}.png`}
                className="inline-block t-small font-semibold text-slate-900 underline underline-offset-4 decoration-slate-300 hover:decoration-slate-900">
                카드 이미지 내려받기
              </a>
            </section>
          ) : null}
        </section>
      </div>

      {registerOpen && testedMacro ? (
        <RegisterMacroModal
          key="studio-register"
          open
          reviewOnly
          initialMacro={testedMacro}
          initialMode={registrationMode}
          onClose={() => setRegisterOpen(false)}
          onDone={finishRegistration}
        />
      ) : null}

      <ProductTour steps={TOUR_STEPS} open={tourOpen} onClose={() => setTourOpen(false)} />
    </div>
  );
}
