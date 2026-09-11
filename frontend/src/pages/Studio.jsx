import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Builder from "../components/Builder.jsx";
import SimBadge from "../components/SimBadge.jsx";
import { StudioTabs, StudioBacktest, StudioAiExplain, StudioOptimize, StudioPaper, StudioOutcomes } from "../components/StudioDock.jsx";
import usePaperSession from "../hooks/usePaperSession.js";
import useStudioSplit from "../hooks/useStudioSplit.js";
import CandleChart from "../components/CandleChart.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import { EmptyState, Loading } from "../components/Page.jsx";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import {
  CANDLE_INTERVALS,
  PERIOD_PRESETS,
  RULE_TYPES,
  buildMacro,
  defaultForm,
  macroToForm,
  validate,
  withTypeDefaults,
  validateDetailed,
} from "../lib/macro.js";
import { computeStrategyOverlay } from "../lib/indicators.js";
import {
  completeJourney,
  peekRegistrationDraft,
  readHeroDraft,
  takeRegistrationDraft,
} from "../lib/journey.js";
import { readStudioSession, writeStudioSession } from "../lib/studioSession.js";
import { backtestBudget, validBacktestLimits } from "../lib/backtestBudget.js";
import "./Studio.css";
import "./StudioBudget.css";
import "./StudioSplit.css";

const MAX_MACRO_FILE_BYTES = 2 * 1024 * 1024;

function macroKey(macro) {
  return JSON.stringify(macro);
}

function periodLabelOf(macro) {
  if (macro.period?.preset === "custom") {
    return `${macro.period.start || "?"} ~ ${macro.period.end || "?"}`;
  }
  return PERIOD_PRESETS.find((item) => item.value === macro.period?.preset)?.label || macro.period?.preset || "";
}

// 매크로 파일 등록 아이콘 — 트레이 위로 올라가는 화살표.
function UploadIcon() {
  return (
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 16V3M7 8l5-5 5 5M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    </svg>
  );
}

// 저장·공유 — 매크로 등록 탭에서 여는 다이얼로그. 링크·인증 카드는 본문이 아니라 부속 결과라 화면에 늘 두지 않는다.
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
  const split = useStudioSplit();
  const { token } = useAuth();
  const { slug } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const entryQuery = searchParams.toString();
  // 다른 화면에 다녀와도 조건·결과·탭이 남아 있도록 — 이 탭(sessionStorage)에 둔 작업 상태로 시작한다.
  // 공유 링크(/s/:slug)는 그 링크의 매크로가 우선이라 읽지 않는다.
  const savedRef = useRef(undefined);
  if (savedRef.current === undefined) savedRef.current = slug ? null : readStudioSession();
  const saved = savedRef.current;
  const [form, setForm] = useState(() => saved?.form || defaultForm());
  const [result, setResult] = useState(() => saved?.result || null);
  const [testedMacro, setTestedMacro] = useState(() => saved?.testedMacro || null);
  const [perSymbol, setPerSymbol] = useState(() => saved?.perSymbol || []);
  const [explanation, setExplanation] = useState(() => saved?.explanation || null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState("");
  const [summary, setSummary] = useState(() => saved?.summary || "");
  const [dataSource, setDataSource] = useState(() => saved?.dataSource || "");
  const [periodLabel, setPeriodLabel] = useState(() => saved?.periodLabel || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [share, setShare] = useState(() => saved?.share || null);
  const [loadedFrom, setLoadedFrom] = useState(() => saved?.loadedFrom || "");
  const [runLeverage, setRunLeverage] = useState(() => saved?.runLeverage || 1);
  const [autoRun, setAutoRun] = useState(() => saved?.autoRun ?? true);
  const [registerOpen, setRegisterOpen] = useState(false);
  const [registrationMode, setRegistrationMode] = useState("live");
  const [hasRegistrationDraft, setHasRegistrationDraft] = useState(
    () => {
      const draft = peekRegistrationDraft();
      return !!draft && draft.context?.origin !== "hero";
    }
  );
  const [dockTab, setDockTab] = useState(() => saved?.dockTab || "bt"); // 결과 독의 탭 — 백테스트 → AI 해설 → 최적화 → 페이퍼 → 매크로 등록
  const [shareOpen, setShareOpen] = useState(false); // 저장·공유 다이얼로그
  const [optimized, setOptimized] = useState(() => !!saved?.optimized); // 최적화를 한 번이라도 돌렸는지(탭 앞 점)
  const [fileImportBusy, setFileImportBusy] = useState(false);
  const [fileImportError, setFileImportError] = useState("");
  const [fileImportSuccess, setFileImportSuccess] = useState("");
  const macroFileInputRef = useRef(null);
  const requestIdRef = useRef(0);
  // 복원한 결과가 지금 조건과 같으면 자동 테스트가 바로 다시 돌지 않게 마지막 시도 키를 맞춰 둔다.
  const lastAttemptKeyRef = useRef(saved?.testedMacro ? macroKey(saved.testedMacro) : "");
  const aiRequestIdRef = useRef(0);
  const latestTestedKeyRef = useRef("");
  const resumeRequestIdRef = useRef(0);
  const processedEntryQueryRef = useRef("");
  const [testLimits, setTestLimits] = useState(null);
  const [limitsError, setLimitsError] = useState("");
  const loadTestLimits = useCallback(async () => {
    try {
      const value = await api.backtestLimits();
      if (!validBacktestLimits(value)) throw new Error("invalid backtest limits");
      setTestLimits(value);
      setLimitsError("");
      return value;
    } catch (_) {
      const message = "테스트 범위를 확인하지 못했어요. 다시 시도해 주세요.";
      setLimitsError(message);
      throw new Error(message);
    }
  }, []);
  useEffect(() => { loadTestLimits().catch(() => {}); }, [loadTestLimits]);
  const testBudget = useMemo(() => backtestBudget(form, testLimits), [form, testLimits]);
  const budgetBlocked = !!testBudget && !testBudget.allowed;
  // 봉 간격 선택지 — 지금 테스트 기간에서 봉 수 한도를 넘는 간격은 고를 수 없게. 한도 숫자는 보여 주지 않는다.
  const intervalOptions = useMemo(() => CANDLE_INTERVALS.map((option) => {
    if (!testLimits) return option;
    const budget = backtestBudget({ ...form, candle_interval: option.value }, testLimits);
    const blocked = !!budget && !budget.allowed && !budget.error;
    return blocked ? { ...option, disabled: true, title: "이 테스트 기간에서는 봉이 너무 많아 고를 수 없어요" } : option;
  }), [form, testLimits]);
  const disabledIntervals = useMemo(() => intervalOptions.filter((option) => option.disabled).map((option) => ({ value: option.value, title: option.title })), [intervalOptions]);
  // 기간을 늘려 지금 간격이 불가능해지면 가능한 다음 간격으로 옮긴다 — 불가능한 조합을 들고 있지 않게.
  useEffect(() => {
    if (!testBudget || testBudget.allowed || testBudget.error) return;
    const larger = testBudget.suggestions.find((choice) => choice.kind === "interval");
    if (larger) setForm((previous) => ({ ...previous, ...larger.patch }));
  }, [testBudget]);

  // 입력 검증 — 걸린 칸(fieldError.field)은 조건 판에서 노랗게 띄우고 라벨 아래 문구를 적는다. 바닥 경고 상자에는 올리지 않는다.
  const fieldError = validateDetailed(form);
  const valErr = fieldError?.message ?? null;
  const fieldErrorKey = fieldError?.field || "";
  useEffect(() => {
    if (!fieldErrorKey) return;
    const host = document.querySelector(`.studio-cond-body [data-field="${fieldErrorKey}"]`);
    if (!host) return;
    const reduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    host.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
    // 다른 칸에 입력 중이면 커서를 뺏지 않는다 — 스크롤과 노랑 표시만.
    const active = document.activeElement;
    const typing = active && ["INPUT", "SELECT", "TEXTAREA"].includes(active.tagName) && !host.contains(active);
    if (typing) return;
    host.querySelector('input:not([type="checkbox"]):not([disabled]), select:not([disabled]), button:not([disabled])')?.focus({ preventScroll: true });
  }, [fieldErrorKey]);
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

  const paper = usePaperSession({ macro: currentMacro, valErr, resumeKey: slug ? "" : "ggp_studio_paper:v1" });

  // 작업 상태 저장 — 값이 바뀌고 300ms 뒤에 한 번. 결과(자산곡선 365점)까지 함께 둔다.
  useEffect(() => {
    if (slug) return undefined;
    const timer = window.setTimeout(() => writeStudioSession({
      form, result, testedMacro, perSymbol, explanation, summary, dataSource, periodLabel, share, loadedFrom, runLeverage, autoRun, dockTab, optimized,
    }), 300);
    return () => window.clearTimeout(timer);
  }, [slug, form, result, testedMacro, perSymbol, explanation, summary, dataSource, periodLabel, share, loadedFrom, runLeverage, autoRun, dockTab, optimized]);
  // 차트 오버레이 — 지금 매크로 설정 그대로 보조지표(볼린저 밴드·매수/매도 구간 등)를 얹는다. form 이 바뀌면 즉시 따라간다.
  const overlay = useCallback((candles) => computeStrategyOverlay(form, candles), [form]);

  // 페이퍼가 돌기 시작하면 그 탭으로, 결과가 사라지면(파일 등록 등) 백테스트 탭으로.
  useEffect(() => { if (paper.running) setDockTab("paper"); }, [paper.running]);
  useEffect(() => { if (!result) { setDockTab("bt"); setOptimized(false); } }, [result]);

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
      const budget = backtestBudget(snapshot, await loadTestLimits());
      if (!budget?.allowed) {
        if (budget?.error) setError(budget.error);
        return false;
      }
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
  }, [loadTestLimits]);

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
    if (!autoRun || !testedMacro || valErr || budgetBlocked || busy || registerOpen) return;
    if (currentMacroKey === lastAttemptKeyRef.current) return;
    const snapshot = form;
    const timer = window.setTimeout(() => runBacktest(snapshot), 700);
    return () => window.clearTimeout(timer);
  }, [autoRun, busy, budgetBlocked, currentMacroKey, form, registerOpen, runBacktest, testedMacro, valErr]);

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
      const budget = backtestBudget(form, await loadTestLimits());
      if (!budget?.allowed) {
        if (budget?.error) setError(budget.error);
        return false;
      }
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
  const shareStale = !!share && !!share.macroKey && share.macroKey !== currentMacroKey;
  // 공유 링크 — 있으면 다이얼로그를 열고, 없거나 조건이 바뀌었으면 저장해서 만든 뒤 연다.
  async function openShare() {
    if (share && !shareStale) { setShareOpen(true); return; }
    const ok = await saveAndShare();
    if (ok) setShareOpen(true);
  }
  const testPrimary = !busy && (!testedMacro || !resultIsFresh);
  const testLabel = busy
    ? "결과 계산 중…"
    : !testedMacro
      ? "이 조건으로 백테스트"
      : resultIsFresh
        ? "자동 실행 완료"
        : "바뀐 조건으로 다시 테스트";
  const tabs = [
    { id: "bt", label: "백테스트", enabled: true, dot: busy ? "run" : !result ? "" : resultIsFresh ? "ok" : "warn" },
    { id: "ai", label: "껄무새 AI 해설", enabled: !!result, dot: aiBusy ? "run" : explanation?.source === "ai" ? "ok" : "" },
    { id: "opt", label: "익·손절 최적화", enabled: !!result, dot: optimized ? "ok" : "" },
    { id: "paper", label: "페이퍼 트레이딩", enabled: !!result, dot: paper.running ? "run" : paper.status ? "ok" : "" },
    { id: "done", label: "매크로 등록", enabled: !!result, dot: "" },
  ];

  function startPaper() {
    setDockTab("paper");
    paper.start();
  }

  // 독 오른쪽의 다음 행동 — 노란 버튼은 화면에 하나뿐이다(§1-4). 결과가 최신이면 조건 판의
  // 백테스트 버튼이 2차로 내려가고 여기의 '페이퍼 트레이딩 시작'이 노랑을 받는다.
  let dockCta = null;
  if (paper.running) {
    dockCta = <button type="button" onClick={() => setDockTab("done")} className="btn btn-m btn-secondary">매크로 등록으로 →</button>;
  } else if (result && resultIsFresh && dockTab !== "done") {
    dockCta = (
      <>
        <span className="t-caption studio-cta-note">검증 3종 확인했어요?</span>
        <button type="button" onClick={startPaper} disabled={paper.busy || !!valErr} className="btn btn-m btn-primary">
          {paper.busy ? "시작 중…" : "페이퍼 트레이딩 시작"}
        </button>
      </>
    );
  }
  const limitsRetry = limitsError ? (
    <button type="button" className="btn btn-s btn-secondary" onClick={() => loadTestLimits().catch(() => {})}>다시 확인</button>
  ) : null;
  const footAlert = (() => {
    if (limitsError && (!error || error === limitsError)) return { tone: "risk", text: limitsError, actions: limitsRetry };
    if (error) return { tone: "risk", text: `오류: ${error}`, actions: error === limitsError ? limitsRetry : null };
    if (valErr && !fieldErrorKey) return { tone: "warn", text: valErr, actions: null }; // 칸이 정해진 오류는 그 칸에 표시된다
    if (budgetBlocked) {
      const text = testBudget.error || `테스트 범위를 넘어요 · ${testBudget.bars.toLocaleString()}봉 / 최대 ${testBudget.maxBars.toLocaleString()}봉`;
      const actions = testBudget.suggestions.length > 0 ? (
        <span className="studio-cond-alert-actions">
          {testBudget.suggestions.map((choice) => (
            <button type="button" key={choice.kind} className="btn btn-s btn-secondary" disabled={busy} onClick={() => { setForm((previous) => ({ ...previous, ...choice.patch })); setError(""); }}>
              {choice.kind === "interval"
                ? `${CANDLE_INTERVALS.find((item) => item.value === choice.value)?.label || choice.value}봉으로`
                : `${PERIOD_PRESETS.find((item) => item.value === choice.value)?.label || choice.value}로`}
            </button>
          ))}
        </span>
      ) : null;
      return { tone: "warn", text, actions };
    }
    return null;
  })();

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

      {(hasRegistrationDraft || fileImportError || fileImportSuccess || loadedFrom) && (
        <div className="studio-banner">
          {loadedFrom && <p className="t-small text-slate-700">불러온 매크로 · <b className="text-slate-900">{loadedFrom}</b></p>}
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

      <div ref={split.workRef} className="studio-work" data-conditions-collapsed={split.collapsed} style={{ "--studio-condition-width": `${split.width}px` }}>
        {/* ── 조건 ── */}
        <aside id="studio-conditions" className="studio-cond" aria-label="조건" {...split.panelProps}>
          <div className="studio-panel-head">
            <h2 className="t-h2 text-slate-900">조건</h2>
            <div className="studio-head-right">
              {/* 매크로 파일 등록 — 가지고 있는 .ggm.json 을 내 매크로에 등록하고 조건에 불러온다. 로그인 전엔 로그인으로. */}
              {!slug && (token ? (
                <button
                  type="button"
                  className="studio-cond-upload t-caption"
                  onClick={() => macroFileInputRef.current?.click()}
                  disabled={fileImportBusy || busy}
                  aria-label={fileImportBusy ? "매크로 업로드 중" : "매크로 업로드 (.ggm.json)"}
                  title={fileImportBusy ? "업로드 중…" : "매크로 업로드 (.ggm.json)"}
                >
                  <UploadIcon />
                  <span>{fileImportBusy ? "업로드 중…" : "매크로 업로드"}</span>
                </button>
              ) : (
                <Link to="/login?next=%2Fbuilder" className="studio-cond-upload t-caption" aria-label="로그인 후 매크로 업로드" title="매크로 파일을 등록하려면 로그인이 필요해요"><UploadIcon /><span>매크로 업로드</span></Link>
              ))}
            </div>
          </div>
          <div className="studio-scroll studio-cond-body">
            <Builder form={form} setForm={setForm} variant="dense" intervalOptions={intervalOptions} fieldError={fieldError} />
          </div>
          <div className="studio-cond-foot">
            {/* 안내·오류는 한 번에 하나, 경고 상자 하나로 — 오류 > 범위 확인 실패 > 입력 오류 > 범위 초과 > 조건 바뀜. */}
            {footAlert && (
              <div className={"alert studio-cond-alert " + (footAlert.tone === "risk" ? "alert-risk" : "alert-warn")} role={footAlert.tone === "risk" ? "alert" : "status"}>
                <span className="studio-cond-alert-text">{footAlert.text}</span>
                {footAlert.actions}
              </div>
            )}
            <button
              type="button"
              onClick={() => runBacktest(form)}
              disabled={busy || !!valErr || budgetBlocked}
              className={"btn btn-l w-full " + (testPrimary ? "btn-primary" : "btn-secondary")}
            >
              {testLabel}
            </button>
            <div className="studio-foot-note t-caption text-slate-500">
              <label className="flex items-center gap-2 t-caption text-slate-700 cursor-pointer select-none whitespace-nowrap">
                <input type="checkbox" checked={autoRun} onChange={(event) => setAutoRun(event.target.checked)} />
                자동 실행
              </label>
              <span>
                <kbd className="num rounded border border-slate-300 bg-slate-100 px-1">Ctrl</kbd>+<kbd className="num rounded border border-slate-300 bg-slate-100 px-1">Enter</kbd>
              </span>
            </div>
          </div>
        </aside>

        <div className="studio-splitter-track">
          <div className="studio-splitter" {...split.separatorProps} />
          <button type="button" className="studio-conditions-reopen" {...split.reopenProps}>
            <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m9 5 7 7-7 7" /></svg>
          </button>
        </div>

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
                  disabledIntervals={disabledIntervals}
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
            <StudioTabs tabs={tabs} active={dockTab} onChange={setDockTab} />
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
                  {/* 조건이 바뀐 경우의 안내는 독 머리(다음 행동 자리)에 한 줄로 — 본문 위에 띠를 얹지 않는다. */}
                  <StudioBacktest
                    result={result}
                    perSymbol={perSymbol}
                    periodLabel={periodLabel}
                    symbol={testedMacro?.symbol || form.symbol}
                    leverage={runLeverage}
                  />
                </>
              )
            )}

            {dockTab === "ai" && result && (
              <StudioAiExplain explanation={explanation} onAiExplain={enrichExplanation} aiBusy={aiBusy} aiError={aiError} />
            )}

            {dockTab === "opt" && result && (
              form.rule_type === "A" ? (
                <StudioOptimize form={form} setForm={setForm} valErr={valErr} onResult={() => setOptimized(true)} />
              ) : (
                <EmptyState title="이 매매 방식은 자동 최적화를 지원하지 않아요">
                  익절/손절 자동 최적화는 <b className="text-slate-900">A · 익절/손절 후 재진입</b>에서만 돌릴 수 있어요.
                </EmptyState>
              )
            )}

            {dockTab === "paper" && result && (
              <StudioPaper macro={currentMacro} valErr={valErr} controller={paper} />
            )}

            {dockTab === "done" && result && (
              <StudioOutcomes
                macro={testedMacro || currentMacro}
                valErr={valErr}
                strategyEntry={{ symbol: chartSymbols[0] || form.symbol || "—", human_summary: summary, macro: testedMacro || currentMacro, locked: false }}
                result={result}
                periodLabel={periodLabel}
                dataSource={dataSource}
                symbols={chartSymbols}
                canRegister={resultIsFresh}
                onRegister={() => openRegistration(paper.mode)}
                onShare={openShare}
                shareBusy={busy}
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

    </div>
  );
}
