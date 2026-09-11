import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Builder from "../components/Builder.jsx";
import ResultView, { ParrotExplain } from "../components/ResultView.jsx";
import SimBadge from "../components/SimBadge.jsx";
import { PaperPanelView, PaperNextSteps } from "../components/PaperPanel.jsx";
import usePaperSession from "../hooks/usePaperSession.js";
import CandleChart from "../components/CandleChart.jsx";
import OptimizePanel from "../components/OptimizePanel.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import ProductTour from "../components/ProductTour.jsx";
import { EmptyState, Loading } from "../components/Page.jsx";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import {
  CANDLE_INTERVALS,
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
import "./Studio.css";

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

// 저장·공유 — ⋯ 메뉴에서 여는 다이얼로그. 링크·인증 카드는 본문이 아니라 부속 결과라 화면에 늘 두지 않는다.
function ShareDialog({ share, stale, busy, onClose, onRenew }) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    const onKey = (event) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);
  async function copy() {
    try {
      await navigator.clipboard.writeText(share.url);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      window.prompt("공유 링크예요. 복사해 주세요.", share.url);
    }
  }
  return createPortal(
    <div className="scrim fixed inset-0 z-[90] grid place-items-center p-4" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby="studio-share-title" className="dialog confirm-dialog studio-share">
        <h2 id="studio-share-title" className="t-h4 text-slate-900">저장·공유</h2>
        {stale ? (
          <div className="notice-warn mt-3 t-small text-slate-700">이 링크는 저장 당시 설정을 가리켜요. 지금 바꾼 조건을 공유하려면 새 링크를 만들어 주세요.</div>
        ) : (
          <p className="mt-3 t-small text-slate-700">지금 조건과 백테스트 결과가 저장됐어요. 링크를 받은 사람은 같은 설정을 불러와 이어서 볼 수 있어요.</p>
        )}
        <div className="mt-4 flex gap-2">
          <input readOnly value={share.url} aria-label="공유 링크" className="field field-sm flex-1" onFocus={(event) => event.target.select()} />
          <button type="button" onClick={copy} className="btn btn-m btn-secondary shrink-0">{copied ? "복사했어요" : "링크 복사"}</button>
        </div>
        <img src={api.cardUrl(share.slug)} alt="공유용 백테스트 인증 카드" className="mt-4" />
        <a
          href={api.cardUrl(share.slug)}
          download={`${share.slug}.png`}
          className="mt-3 inline-block t-small font-semibold text-slate-900 underline underline-offset-4 decoration-slate-300 hover:decoration-slate-900"
        >
          카드 이미지 내려받기
        </a>
        <div className="confirm-dialog-actions">
          {stale && (
            <button type="button" onClick={onRenew} disabled={busy} className="btn btn-l w-full btn-primary">
              {busy ? "저장 중…" : "지금 조건으로 새 링크 만들기"}
            </button>
          )}
          <button type="button" onClick={onClose} className="btn btn-l w-full btn-ghost">닫기</button>
        </div>
      </div>
    </div>,
    document.body,
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
  const [dockTab, setDockTab] = useState("bt"); // 결과 독의 탭 — 백테스트 → AI 해설 → 최적화 → 페이퍼 → 등록·실행
  const [menuOpen, setMenuOpen] = useState(false); // ⋯ 메뉴(파일 등록 · 공유 · 사용법)
  const [shareOpen, setShareOpen] = useState(false); // 저장·공유 다이얼로그
  const [optimized, setOptimized] = useState(false); // 최적화를 한 번이라도 돌렸는지(탭 앞 점)
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

  const paper = usePaperSession({ macro: currentMacro, valErr });
  // 차트 오버레이 — 지금 매크로 설정 그대로 보조지표(볼린저 밴드·매수/매도 구간 등)를 얹는다. form 이 바뀌면 즉시 따라간다.
  const overlay = useCallback((candles) => computeStrategyOverlay(form, candles), [form]);

  // 페이퍼가 돌기 시작하면 그 탭으로, 결과가 사라지면(파일 등록 등) 백테스트 탭으로.
  useEffect(() => { if (paper.running) setDockTab("paper"); }, [paper.running]);
  useEffect(() => { if (!result) { setDockTab("bt"); setOptimized(false); } }, [result]);

  // ⋯ 메뉴 — 바깥 클릭·Esc 로 닫는다.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onPointerDown = (event) => { if (!event.target.closest?.(".studio-more")) setMenuOpen(false); };
    const onKey = (event) => { if (event.key === "Escape") setMenuOpen(false); };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

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
    if (valErr) { setError(valErr); return false; }
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
      return true;
    } catch (reason) {
      if (requestId === requestIdRef.current) setError(String(reason.message || reason));
      return false;
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

  // ── 워크벤치 ──────────────────────────────────────────────────────────
  // 페이지는 스크롤하지 않는다. 조건(왼쪽) · 차트(오른쪽 위) · 결과 독(오른쪽 아래)이 각자 스크롤한다.
  // 결과 독의 탭 순서가 곧 검증 순서다: 백테스트 → AI 해설 → 익·손절 최적화 → 페이퍼 트레이딩 → 등록·실행.
  const ruleLabel = RULE_TYPES[form.rule_type]?.label || "";
  const intervalLabel = CANDLE_INTERVALS.find((item) => item.value === form.candle_interval)?.label || "";
  const stage = dockTab === "done" || (paper.status && !paper.running) ? 3 : paper.running ? 2 : result ? 1 : 0;
  const shareStale = !!share && !!share.macroKey && share.macroKey !== currentMacroKey;
  const paperReturn = paper.status?.current_return;
  const testPrimary = !busy && (!testedMacro || !resultIsFresh);
  const testLabel = busy
    ? "결과 계산 중…"
    : !testedMacro
      ? "이 조건으로 백테스트"
      : resultIsFresh
        ? "결과 최신 · 바꾸면 다시 계산해요"
        : "바뀐 조건으로 다시 테스트";
  const tabs = [
    { id: "bt", label: "백테스트", enabled: true, dot: busy ? "run" : !result ? "" : resultIsFresh ? "ok" : "warn" },
    { id: "ai", label: "껄무새 AI 해설", enabled: !!result, dot: aiBusy ? "run" : explanation?.source === "ai" ? "ok" : "" },
    { id: "opt", label: "익·손절 최적화", enabled: !!result, dot: optimized ? "ok" : "" },
    { id: "paper", label: "페이퍼 트레이딩", enabled: !!result, dot: paper.running ? "run" : paper.status ? "ok" : "" },
    { id: "done", label: "등록 · 실행", enabled: !!result, dot: "" },
  ];

  function startPaper() {
    setDockTab("paper");
    paper.start();
  }

  // 독 오른쪽의 다음 행동 — 노란 버튼은 화면에 하나뿐이다(§1-4). 결과가 최신이면 조건 판의
  // 백테스트 버튼이 2차로 내려가고 여기의 '페이퍼 트레이딩 시작'이 노랑을 받는다.
  let dockCta = null;
  if (paper.running) {
    dockCta = (
      <>
        <span className="t-caption studio-cta-note">
          페이퍼 진행 중
          {paperReturn != null && (
            <> · <b className={"num " + (paperReturn >= 0 ? "text-green-600" : "text-red-600")}>{paperReturn >= 0 ? "+" : ""}{Number(paperReturn).toFixed(2)}%</b></>
          )}
        </span>
        <button type="button" onClick={() => setDockTab("done")} className="btn btn-m btn-secondary">등록 · 실행으로 →</button>
      </>
    );
  } else if (result && resultIsFresh && dockTab !== "done") {
    dockCta = (
      <>
        <span className="t-caption studio-cta-note">검증 3종 확인했어요?</span>
        <button type="button" onClick={startPaper} disabled={paper.busy || !!valErr} className="btn btn-m btn-primary">
          {paper.busy ? "시작 중…" : "페이퍼 트레이딩 시작"}
        </button>
      </>
    );
  } else if (result && !resultIsFresh) {
    dockCta = <span className="t-caption studio-cta-note">조건이 바뀌었어요 · 다시 테스트하면 이어져요</span>;
  }

  return (
    <div className="studio-page">
      <input
        ref={macroFileInputRef}
        type="file"
        accept=".json,.ggm.json,application/json"
        onChange={onMacroFileChange}
        className="sr-only"
        aria-label="껄무새 매크로 파일 등록"
      />

      {/* 제목 띠 — 제목 · 지금 조건 한 줄 · 모의 뱃지 · 단계 · ⋯. 설명 문단은 두지 않는다. */}
      <header className="studio-strip">
        <h1 className="t-h4 text-slate-900">매크로 만들기</h1>
        <span className="studio-strip-meta t-caption">
          <b className="num">{chartSymbols.length === 0 ? "종목 없음" : chartSymbols.join(", ")}</b>
          {ruleLabel && <> · {ruleLabel}</>}
          {intervalLabel && <> · {intervalLabel}봉</>}
          {periodLabelOf(currentMacro) && <> · {periodLabelOf(currentMacro)}</>}
        </span>
        {loadedFrom && <span className="studio-strip-loaded t-caption">불러온 매크로 · <b>{loadedFrom}</b></span>}
        <SimBadge />
        <ol className="studio-flow" aria-label="진행 단계">
          {["조건", "검증 3종", "페이퍼 트레이딩", "등록 · 실행"].map((label, index) => (
            <li key={label} className={index < stage ? "is-done" : index === stage ? "is-now" : ""} aria-current={index === stage ? "step" : undefined}>
              <i aria-hidden="true" />{label}
            </li>
          ))}
        </ol>
        <div className="studio-more">
          <button
            type="button"
            className="studio-more-btn"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            aria-label="더 보기 — 파일 등록 · 공유 · 사용법"
            onClick={() => setMenuOpen((open) => !open)}
          >
            ⋯
          </button>
          {menuOpen && (
            <div className="studio-menu" role="menu">
              {!slug && (token ? (
                <button
                  type="button"
                  role="menuitem"
                  disabled={fileImportBusy || busy}
                  onClick={() => { setMenuOpen(false); macroFileInputRef.current?.click(); }}
                >
                  {fileImportBusy ? "파일 등록 중…" : "매크로 파일 등록"}<small>.ggm.json</small>
                </button>
              ) : (
                <Link to="/login?next=%2Fbuilder" role="menuitem" onClick={() => setMenuOpen(false)}>
                  로그인 후 파일 등록<small>.ggm.json</small>
                </Link>
              ))}
              <button
                type="button"
                role="menuitem"
                disabled={busy || !!valErr}
                onClick={async () => {
                  setMenuOpen(false);
                  if (share && !shareStale) { setShareOpen(true); return; }
                  const ok = await saveAndShare();
                  if (ok) setShareOpen(true);
                }}
              >
                {share && !shareStale ? "공유 링크 보기" : "저장하고 공유 링크 만들기"}<small>인증 카드</small>
              </button>
              <button type="button" role="menuitem" onClick={() => { setMenuOpen(false); setTourOpen(true); }}>
                사용법 안내<small>화면 순서대로</small>
              </button>
            </div>
          )}
        </div>
      </header>

      {(hasRegistrationDraft || fileImportError || fileImportSuccess) && (
        <div className="studio-banner">
          {hasRegistrationDraft && (
            <div className="notice t-small text-slate-700 flex items-center justify-between gap-4 flex-wrap">
              <span>로그인 화면으로 가기 전에 테스트한 등록 설정이 남아 있어요.</span>
              <button type="button" onClick={() => navigate("/builder?guide=1&register=1")} className="btn btn-s btn-secondary">
                등록 계속하기
              </button>
            </div>
          )}
          {fileImportError && <p className="t-small text-red-600" role="alert">{fileImportError}</p>}
          {fileImportSuccess && <p className="t-small text-green-700" role="status">{fileImportSuccess}</p>}
        </div>
      )}

      <div className="studio-work">
        {/* ── 조건 ── */}
        <aside className="studio-cond" aria-label="조건">
          <div className="studio-panel-head">
            <h2 className="t-title text-slate-900">조건</h2>
            <div className="studio-head-right">
              <label className="flex items-center gap-2 t-caption text-slate-700 cursor-pointer select-none">
                <input type="checkbox" checked={autoRun} onChange={(event) => setAutoRun(event.target.checked)} />
                변경 뒤 자동 테스트
              </label>
            </div>
          </div>
          <div className="studio-scroll studio-cond-body">
            <Builder form={form} setForm={setForm} variant="dense" />
          </div>
          <div className="studio-cond-foot">
            {valErr && <div className="t-small text-amber-700" role="alert">{valErr}</div>}
            {error && <div className="t-small text-red-600" role="alert">오류: {error}</div>}
            <button
              type="button"
              onClick={() => runBacktest(form)}
              disabled={busy || !!valErr}
              className={"btn btn-l w-full " + (testPrimary ? "btn-primary" : "btn-secondary")}
            >
              {testLabel}
            </button>
            <div className="studio-foot-note t-caption text-slate-500">
              <span>첫 결과 뒤부터 자동 테스트가 동작해요</span>
              <span>
                <kbd className="num rounded border border-slate-300 bg-slate-100 px-1">Ctrl</kbd>+<kbd className="num rounded border border-slate-300 bg-slate-100 px-1">Enter</kbd>
              </span>
            </div>
          </div>
        </aside>

        {/* ── 차트 — 주인공. 조건을 바꾸면 보조지표·익절/손절선이 바로 따라온다.
            판 머리는 따로 두지 않는다 — CandleChart(studio) 의 도구줄(종목·시세·봉 간격·범례)이 곧 머리다. ── */}
        <section className="studio-chart" aria-label="실시간 차트 · 보조지표" data-tour="chart">
          <div className={"studio-chart-body" + (chartSymbols.length > 1 ? " is-multi" : "")}>
            {chartSymbols.length > 0 ? (
              // 시장은 넘기지 않는다(= 현물). 보조지표는 rule_type·포지션·전략 조건만 보고 그려져
              // 레버리지와 무관하다. interval 은 controlled — 조건의 '봉 간격'과 차트 툴바가 같은 값을 가리킨다.
              chartSymbols.map((symbol) => (
                <CandleChart
                  key={symbol}
                  symbol={symbol}
                  interval={form.candle_interval || "1m"}
                  onIntervalChange={(value) => setForm((f) => ({ ...f, candle_interval: value }))}
                  overlay={overlay}
                  variant="studio"
                />
              ))
            ) : (
              <EmptyState title="종목을 입력하면 차트가 나와요">왼쪽 조건의 종목 칸에 BTCUSDT 처럼 적어 주세요.</EmptyState>
            )}
          </div>
        </section>

        {/* ── 결과 독 ── */}
        <section className="studio-dock" aria-label="결과">
          <div className="studio-dock-head">
            <div className="seg" role="tablist" aria-label="결과 보기">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  role="tab"
                  id={`studio-tab-${tab.id}`}
                  aria-selected={dockTab === tab.id}
                  aria-controls="studio-dock-panel"
                  disabled={!tab.enabled}
                  title={!tab.enabled ? "백테스트 결과가 있어야 볼 수 있어요" : undefined}
                  onClick={() => setDockTab(tab.id)}
                  className={"seg-item " + (dockTab === tab.id ? "seg-item-on" : "")}
                >
                  <i className={"studio-dot" + (tab.dot ? ` is-${tab.dot}` : "")} aria-hidden="true" />
                  {tab.label}
                </button>
              ))}
            </div>
            <div className="studio-dock-cta">{dockCta}</div>
          </div>
          <div className="studio-scroll studio-dock-body" role="tabpanel" id="studio-dock-panel" aria-labelledby={`studio-tab-${dockTab}`}>
            <div className="sr-only" role="status" aria-live="polite">
              {busy ? "백테스트 결과를 계산하고 있어요." : resultIsFresh ? "백테스트 결과가 준비됐어요." : ""}
            </div>

            {dockTab === "bt" && (
              !result ? (
                busy ? (
                  <Loading label="백테스트 결과를 계산하고 있어요…" />
                ) : (
                  <EmptyState title="아직 계산한 결과가 없어요">
                    왼쪽 조건을 고른 뒤 <b className="text-slate-900">이 조건으로 백테스트</b>를 누르면 여기와 차트에 바로 나와요.
                  </EmptyState>
                )
              ) : (
                <>
                  {!resultIsFresh && (
                    <div className="notice-warn studio-stale t-small text-slate-700">
                      결과를 낸 뒤 조건이 바뀌었어요. 아래 숫자는 이전 조건의 결과이며 등록에는 사용할 수 없어요.
                    </div>
                  )}
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
                    hideBlocks={["ai"]}
                  />
                </>
              )
            )}

            {dockTab === "ai" && result && (
              <ParrotExplain explanation={explanation} onAiExplain={enrichExplanation} aiBusy={aiBusy} aiError={aiError} />
            )}

            {dockTab === "opt" && result && (
              form.rule_type === "A" ? (
                <OptimizePanel form={form} setForm={setForm} valErr={valErr} onResult={() => setOptimized(true)} />
              ) : (
                <EmptyState title="이 매매 방식은 자동 최적화를 지원하지 않아요">
                  익절/손절 자동 최적화는 <b className="text-slate-900">A · 익절/손절 후 재진입</b>에서만 돌릴 수 있어요.
                </EmptyState>
              )
            )}

            {dockTab === "paper" && result && (
              <PaperPanelView
                macro={currentMacro}
                valErr={valErr}
                onRegister={resultIsFresh ? openRegistration : null}
                controller={paper}
                nextSteps={false}
              />
            )}

            {dockTab === "done" && result && (
              <PaperNextSteps
                macro={testedMacro || currentMacro}
                valErr={valErr}
                primary="register"
                onRegister={() => openRegistration(paper.mode)}
                canRegister={resultIsFresh}
                result={result}
                paperStatus={paper.status}
              />
            )}
          </div>
        </section>
      </div>

      {shareOpen && share && (
        <ShareDialog
          share={share}
          stale={shareStale}
          busy={busy}
          onClose={() => setShareOpen(false)}
          onRenew={async () => {
            const ok = await saveAndShare();
            if (!ok) setShareOpen(false);
          }}
        />
      )}

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
