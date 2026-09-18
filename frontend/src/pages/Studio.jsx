import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import Builder from "../components/Builder.jsx";
import SimBadge from "../components/SimBadge.jsx";
import { StudioTabs, StudioBacktest, StudioAiExplain, StudioOptimize, StudioPaper, StudioOutcomes } from "../components/StudioDock.jsx";
import MacroCard from "../components/MacroCard.jsx";
import usePaperSession from "../hooks/usePaperSession.js";
import useStudioSplit from "../hooks/useStudioSplit.js";
import CandleChart from "../components/CandleChart.jsx";
import RegisterMacroModal from "../components/RegisterMacroModal.jsx";
import AskParrotDialog from "../components/AskParrotDialog.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import { LOADED_TEXT } from "../lib/askCopy.js";
import { EmptyState, Loading } from "../components/Page.jsx";
import { api } from "../api.js";
import { useAuth, useAccountGuard, getAuthScope } from "../lib/auth.js";
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
import { readStudioSession, writeStudioSession, studioPaperKey } from "../lib/studioSession.js";
import { backtestBudget, validBacktestLimits } from "../lib/backtestBudget.js";
import { recordEvent } from "../lib/visit.js";
import ProductTour from "../components/ProductTour.jsx";
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

// 빌더 종류 — 조건 판의 제목이 곧 드롭다운(`기본 빌더 ▾`). 지금은 기본 빌더뿐이고 프로 빌더는 업데이트 예정이라 메뉴에 비활성으로만 있다.
// 프로가 열리면 항목의 disabled 를 떼고 고른 값으로 폼을 바꿔 끼운다.
// '사용법 안내' 프로덕트 투어 단계 — 각 anchor 는 조건 판·차트의 data-tour 요소를 가리킨다(cc8ba5e 에서 빠졌던 것을 복원).
const TOUR_STEPS = [
  { anchor: "market-briefing", title: "시장 브리핑", body: "먼저 시장 분위기를 확인해요. ‘시장 브리핑 보기’를 누르면 김치 프리미엄(국내외 가격 차이 · +김프/−역프)과 공포·탐욕 지수(0~100, 시장 심리)를 볼 수 있어요. 매매 전 참고용 지표예요." },
  { anchor: "symbol", title: "종목 검색", body: "확인할 코인을 검색해서 골라요. 예: BTC. 실제 거래되는 종목만 추가되고, 여러 종목을 넣으면 자금을 종목 수만큼 균등하게 나눠 종목마다 따로 돌리고 결과는 총합이에요." },
  { anchor: "strategy", title: "매매 방식 선택", body: "이동평균 크로스·볼린저·RSI 등 원하는 전략을 골라요. 라벨 옆 ⓘ에 마우스를 올리면 각 매매 방식이 어떤 규칙인지 설명을 확인할 수 있어요." },
  { anchor: "position", title: "포지션", body: "오를 때 버는 롱(long), 내릴 때 버는 숏(short)을 정해요. 전략에 따라 숏이 막혀 있을 수 있어요." },
  { anchor: "interval", title: "봉 간격", body: "지표 계산과 체결을 판정하는 캔들 단위예요. 1분·1시간·1일처럼 전략에 맞는 시간 단위를 골라요." },
  { anchor: "period", title: "테스트 기간", body: "과거 어느 구간의 데이터로 확인할지 정해요. 최근 1년·6개월·3개월 또는 직접 기간을 지정할 수 있어요." },
  { anchor: "chart", title: "실시간 차트 · 보조지표", body: "지금 고른 종목의 실시간 시세를 보여줘요. 선택한 매매 방식의 보조지표(예: 이동평균·볼린저 밴드)가 함께 그려지고, 설정값을 바꾸면 보조지표도 즉시 따라 바뀌는 걸 확인할 수 있어요." },
  { anchor: "strategy-params", title: "전략 조건", body: "고른 매매 방식에만 필요한 세부 값을 정해요. 익절 기준·이동평균 기간·밴드 폭처럼 전략마다 항목이 달라져요." },
  { anchor: "risk", title: "손실 제한", body: "한 번에 쓸 자금 비율과 손절 기준(%)을 정해요. 손절을 켜면 정해진 손실에서 자동으로 정리해 위험을 제한해요." },
  { anchor: "advanced-risk", title: "고급 위험 관리", body: "하루 최대 손실·최대 보유 시간·손절 뒤 쉬는 시간 같은 추가 안전장치예요. 필요할 때만 설정하면 돼요." },
  { anchor: "fees", title: "거래 비용과 펀딩비", body: "실제에 가깝게 수수료·체결 가격 차이(슬리피지)·펀딩비를 반영해요. ‘실제 펀딩비 가져오기’로 해당 기간 평균값을 자동으로 채울 수 있어요." },
  { anchor: "leverage", title: "레버리지", body: "배수를 올리면 수익도 손실도 그만큼 커지고 청산 위험이 생겨요. 1배는 현물과 같아 청산이 없어요. 백테스트·모의에서만 적용돼요." },
];

function BuilderModeMenu({ onTour }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const onKey = (event) => { if (event.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [open]);
  return (
    <div className="studio-mode" ref={rootRef}>
      <button type="button" className="studio-mode-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        기본 빌더<i className="studio-mode-chev" aria-hidden="true" />
      </button>
      {open && (
        <div className="studio-mode-menu" role="menu" aria-label="빌더 종류">
          <button type="button" role="menuitemradio" aria-checked="true" className="studio-mode-item is-on" onClick={() => setOpen(false)}>
            <span className="studio-mode-check" aria-hidden="true">✓</span>기본 빌더
          </button>
          <button type="button" role="menuitemradio" aria-checked="false" disabled title="프로 빌더는 업데이트 예정이에요" className="studio-mode-item is-soon">
            <span className="studio-mode-check" aria-hidden="true" />프로 빌더<span className="studio-soon-badge">업데이트 예정</span>
          </button>
          {onTour ? (
            <>
              <hr className="studio-mode-sep" aria-hidden="true" />
              {/* 항목별 설명 투어 — 화면 순서대로 각 칸을 비추며 설명한다. */}
              <button type="button" role="menuitem" className="studio-mode-item" onClick={() => { setOpen(false); onTour(); }}>
                <span className="studio-mode-check" aria-hidden="true">?</span>사용법 안내<small className="studio-mode-hint">화면 순서대로</small>
              </button>
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}

// 저장·공유 — 매크로 등록 탭에서 여는 다이얼로그. 링크·인증 카드는 본문이 아니라 부속 결과라 화면에 늘 두지 않는다.
function ShareDialog({ share, stale, busy, card, onClose, onRenew }) {
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const cardRef = useRef(null);
  // 카드 이미지 — 화면의 트레이딩 카드를 브라우저가 그린 그대로 뜬다(html-to-image: SVG foreignObject 로 같은 렌더링 엔진이 그린다, 2배).
  // 폰트(Pretendard · JetBrains Mono)와 로고 사본(/api/coin-logo)이 모두 같은 출처라 그대로 실린다. 모서리 밖은 투명.
  async function downloadCard() {
    if (!cardRef.current) return;
    setSaving(true); setSaveError("");
    try {
      const { toPng } = await import("html-to-image");
      if (document.fonts?.ready) await document.fonts.ready;
      // 캡처 상자의 바깥 여백(margin-top 16px)이 복제본에도 실려 카드가 아래로 밀리고 바닥이 잘렸다 — 복제본에서는 여백을 0 으로.
      const dataUrl = await toPng(cardRef.current, { pixelRatio: 2, cacheBust: false, style: { margin: "0" } });
      const a = document.createElement("a");
      a.href = dataUrl;
      a.download = `${share.slug}.png`;
      a.click();
    } catch (e) {
      setSaveError("카드 이미지를 만들지 못했어요. 잠시 후 다시 시도해 주세요.");
    } finally {
      setSaving(false);
    }
  }
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
        {card && (
          <div ref={cardRef} className="studio-share-card">
            <MacroCard {...card} logoProxy />
          </div>
        )}
        <div className="mt-3 flex items-center gap-3 flex-wrap">
          <button type="button" onClick={downloadCard} disabled={saving || !card} className="btn btn-m btn-secondary">
            {saving ? "이미지 만드는 중…" : "카드 이미지 내려받기"}
          </button>
          {saveError && <span className="t-small text-red-600" role="alert">{saveError}</span>}
        </div>
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
  const { accountVersion } = useAuth();
  const scope = getAuthScope();
  const location = useLocation();
  const entryOwner = useRef({ key: location.key, scope });
  if (entryOwner.current.key !== location.key) entryOwner.current = { key: location.key, scope };
  return <AccountStudio key={accountVersion} scope={scope} allowRouterMacro={entryOwner.current.scope === scope} />;
}

function AccountStudio({ scope, allowRouterMacro }) {
  const isCurrentAccount = useAccountGuard();
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
  if (savedRef.current === undefined) savedRef.current = slug ? null : readStudioSession(scope);
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
  const [tourOpen, setTourOpen] = useState(false); // '사용법 안내' 항목별 설명 투어
  // 껄무새에게 물어볼까? — 모달, 덮어쓰기 확인, 불러온 뒤 안내
  // 로그아웃 상태로 ?ask=1 이 와도 파라미터는 남겨 둔다 — 로그인 버튼의 next=/builder?ask=1 왕복이 그대로 통하도록.
  const [askOpen, setAskOpen] = useState(() => Boolean(token) && searchParams.get("ask") === "1");
  const [askPending, setAskPending] = useState(null); // {macro, label} — 조건 판에 입력이 있을 때 확인 대기
  const [askNotice, setAskNotice] = useState("");

  const applyAskMacro = useCallback((macro, label) => {
    setForm(macroToForm(macro));
    setLoadedFrom(`껄무새가 고른 후보 · ${label}`);
    setAskPending(null);
    setAskOpen(false);
    setAskNotice(LOADED_TEXT);
    recordEvent("ask_load");
    if (searchParams.get("ask") === "1") {
      const next = new URLSearchParams(searchParams);
      next.delete("ask");
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams]);

  const onAskLoad = useCallback((macro, label) => {
    const untouched = JSON.stringify(form) === JSON.stringify(defaultForm());
    if (untouched) applyAskMacro(macro, label);
    else setAskPending({ macro, label });
  }, [form, applyAskMacro]);

  useEffect(() => {
    if (!askNotice) return undefined;
    const id = setTimeout(() => setAskNotice(""), 8000);
    return () => clearTimeout(id);
  }, [askNotice]);
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
      if (!isCurrentAccount()) throw new DOMException("Account changed", "AbortError");
      if (!validBacktestLimits(value)) throw new Error("invalid backtest limits");
      setTestLimits(value);
      setLimitsError("");
      return value;
    } catch (_) {
      if (!isCurrentAccount()) throw new DOMException("Account changed", "AbortError");
      const message = "테스트 범위를 확인하지 못했어요. 다시 시도해 주세요.";
      setLimitsError(message);
      throw new Error(message);
    }
  }, [isCurrentAccount]);
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

  const paper = usePaperSession({ macro: currentMacro, valErr, resumeKey: slug ? "" : studioPaperKey(scope), accountGuard: isCurrentAccount });

  // 작업 상태 저장 — 값이 바뀌고 300ms 뒤에 한 번. 결과(자산곡선 365점)까지 함께 둔다.
  useEffect(() => {
    if (slug) return undefined;
    const timer = window.setTimeout(() => { if (isCurrentAccount()) writeStudioSession({
      form, result, testedMacro, perSymbol, explanation, summary, dataSource, periodLabel, share, loadedFrom, runLeverage, autoRun, dockTab, optimized,
    }, scope); }, 300);
    return () => window.clearTimeout(timer);
  }, [scope, isCurrentAccount, slug, form, result, testedMacro, perSymbol, explanation, summary, dataSource, periodLabel, share, loadedFrom, runLeverage, autoRun, dockTab, optimized]);
  // 차트 오버레이 — 지금 매크로 설정 그대로 보조지표(볼린저 밴드·매수/매도 구간 등)를 얹는다. form 이 바뀌면 즉시 따라간다.
  const overlay = useCallback((candles) => computeStrategyOverlay(form, candles), [form]);

  // 페이퍼가 돌기 시작하면 그 탭으로, 결과가 사라지면(파일 등록 등) 백테스트 탭으로.
  useEffect(() => { if (paper.running) setDockTab("paper"); }, [paper.running]);
  useEffect(() => { if (!result) { setDockTab("bt"); setOptimized(false); } }, [result]);

  // Clone flow: load a shared macro into the builder, then run its backtest once so the
  // receiver sees results, tabs and the card right away (the link stores conditions only).
  const runBacktestRef = useRef(null);
  useEffect(() => {
    if (!slug) return;
    let alive = true;
    setBusy(true);
    api
      .getMacro(slug)
      .then((data) => {
        if (!alive) return;
        const loadedForm = macroToForm(data.macro);
        setForm(loadedForm);
        setLoadedFrom(data.human_summary);
        setShare({
          slug,
          url: `${window.location.origin}/s/${slug}`,
          macroKey: macroKey(buildMacro(loadedForm)),
        });
        setBusy(false);
        return runBacktestRef.current?.(loadedForm);
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
    if (!macro || !allowRouterMacro) return;
    setForm(macroToForm(macro));
    setLoadedFrom(
      location.state?.source === "hero-guide"
        ? "시작 가이드에서 고른 설정"
        : "리더보드에서 복사한 매크로"
    );
    navigate(location.pathname + location.search, { replace: true, state: null });
  }, [allowRouterMacro, location.pathname, location.search, location.state, navigate]);

  const runBacktest = useCallback(async (snapshot) => {
    if (!isCurrentAccount()) return false;
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
      if (!isCurrentAccount()) return false;
      if (!budget?.allowed) {
        if (budget?.error) setError(budget.error);
        return false;
      }
      // 퍼널 '백테스트 실행' 단계 — 성공·실패와 무관하게 실행을 시작한 사실을 센다(예산에 막힌 시도는 위에서 걸러졌다).
      recordEvent("backtest");
      const data = await api.backtest(macro);
      if (!isCurrentAccount() || requestId !== requestIdRef.current) return false;
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
      if (isCurrentAccount() && requestId === requestIdRef.current) setError(String(reason.message || reason));
      return false;
    } finally {
      if (isCurrentAccount() && requestId === requestIdRef.current) setBusy(false);
    }
  }, [isCurrentAccount, loadTestLimits]);
  runBacktestRef.current = runBacktest;

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
    if (!isCurrentAccount()) return false;
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
      if (!isCurrentAccount()) return false;
      if (!budget?.allowed) {
        if (budget?.error) setError(budget.error);
        return false;
      }
      const macro = currentMacro;
      const data = await api.createMacro(macro);
      if (!isCurrentAccount() || requestId !== requestIdRef.current) return;
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
      if (isCurrentAccount() && requestId === requestIdRef.current) setError(String(reason.message || reason));
      return false;
    } finally {
      if (isCurrentAccount() && requestId === requestIdRef.current) setBusy(false);
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
      if (!isCurrentAccount()) return;
      if (!rawMacro || typeof rawMacro !== "object" || !rawMacro.symbol || !rawMacro.rule_type || !rawMacro.params) {
        throw new Error("INVALID_MACRO_FILE");
      }
      const importedForm = macroToForm(rawMacro);
      const validationError = validate(importedForm);
      if (validationError) throw new Error(validationError);

      const macro = buildMacro(importedForm);
      const name = file.name.replace(/\.ggm\.json$|\.json$/i, "") || `${macro.symbol} 매크로`;
      const data = await api.saveMyMacro(macro, name);
      if (!isCurrentAccount()) return;
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
      if (!isCurrentAccount()) return;
      const message = String(reason.message || reason);
      setFileImportError(
        message === "INVALID_MACRO_FILE" || reason instanceof SyntaxError
          ? "껄무새에서 받은 .ggm.json 파일인지 확인해 주세요."
          : reason?.status === 401
            ? "로그인이 만료됐어요. 다시 로그인한 뒤 등록해 주세요."
            : `매크로 파일을 등록하지 못했어요: ${message}`,
      );
    } finally {
      if (isCurrentAccount()) setFileImportBusy(false);
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
      if (!isCurrentAccount() || requestId !== aiRequestIdRef.current || latestTestedKeyRef.current !== key) return;
      if (data.explanation) setExplanation(data.explanation);
      if (data.ai_available === false) setAiError("AI 해설이 아직 준비되지 않았어요 (서버 설정 필요).");
      else if (data.ai_error) setAiError(data.ai_error);
    } catch (reason) {
      if (isCurrentAccount() && requestId === aiRequestIdRef.current && latestTestedKeyRef.current === key) {
        setAiError("AI 호출 실패: " + String(reason.message || reason));
      }
    } finally {
      if (isCurrentAccount() && requestId === aiRequestIdRef.current) setAiBusy(false);
    }
  }

  function finishRegistration(entry) {
    if (!isCurrentAccount()) return;
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


  const limitsRetry = limitsError ? (
    <button type="button" className="btn btn-s btn-secondary" onClick={() => loadTestLimits().catch(() => {})}>다시 확인</button>
  ) : null;
  // 매크로 카드 재료 — 매크로 등록 탭과 공유 다이얼로그가 같은 카드를 그린다.
  // 카드는 결과와 짝인 '테스트한 매크로'를 보여 준다. 종목도 그 매크로에서 읽는다 — 조건 판에서 종목을 빼도 다시 테스트하기 전엔 카드가 바뀌지 않는다.
  const cardMacro = testedMacro || currentMacro;
  const cardSymbols = Array.isArray(cardMacro.symbols) && cardMacro.symbols.length > 1 ? cardMacro.symbols : [cardMacro.symbol].filter(Boolean);
  const cardProps = {
    macro: cardMacro,
    result,
    perSymbol,
    strategyEntry: { symbol: cardSymbols[0] || "—", human_summary: summary, macro: cardMacro, locked: false },
    periodLabel,
    dataSource,
    symbols: cardSymbols,
  };
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
            <BuilderModeMenu onTour={() => setTourOpen(true)} />
            <div className="studio-head-right">
              {/* 껄무새에게 물어볼까? — 카드 다섯 장으로 후보 조합 3개. 로그인 전엔 로그인으로(기록을 남겨야 해서). */}
              {!slug && (token ? (
                <button type="button" className="studio-cond-upload studio-cond-ask t-caption" onClick={() => { setAskOpen(true); recordEvent("ask_open"); }} disabled={busy} aria-haspopup="dialog" aria-expanded={askOpen}>
                  <span aria-hidden="true">🦜</span><span>껄무새에게 물어볼까?</span>
                </button>
              ) : (
                <Link to="/login?next=%2Fbuilder%3Fask%3D1" className="studio-cond-upload studio-cond-ask t-caption" title="물어보려면 로그인이 필요해요"><span aria-hidden="true">🦜</span><span>껄무새에게 물어볼까?</span></Link>
              ))}
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
            {askNotice ? <div className="notice t-small text-slate-700 mb-3" role="status">{askNotice}</div> : null}
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
                {...cardProps}
                valErr={valErr}
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
          card={result ? cardProps : null}
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

      {token ? <AskParrotDialog open={askOpen} onClose={() => setAskOpen(false)} onLoad={onAskLoad} /> : null}
      <ConfirmDialog
        open={askPending != null}
        title="지금 조건이 바뀌어요"
        description="조건 판에 입력한 값을 껄무새가 고른 후보로 덮어써요. 계속할까요?"
        confirmLabel="불러오기"
        onConfirm={() => askPending && applyAskMacro(askPending.macro, askPending.label)}
        onCancel={() => setAskPending(null)}
      />
    </div>
  );
}
