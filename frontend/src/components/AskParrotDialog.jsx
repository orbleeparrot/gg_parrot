// 껄무새에게 물어볼까? — 카드(칩) 다섯 장으로 답을 받아 백테스트 상위 3개 조합을 보여 준다.
// 자유 입력은 종목 검색 하나뿐. 결과의 숫자는 전부 서버 백테스트가 계산한 값이다.
import { useEffect, useId, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import {
  CONSENT_TEXT, DISCLAIMER, FEW_RESULTS_TEXT, FOLLOW_UPS, INTERVALS, LEVERAGES, MARKETS, NO_RESULTS_TEXT,
  PERIODS, POPULAR_SYMBOLS, PROFILES, RUNNING_TEXT, STABLE_NO_FUTURES, STEP_PROMPTS,
} from "../lib/askCopy.js";
import { MAX_SYMBOLS, STEPS, answerLabel, canChooseFutures, initialState, reduce, toRequest } from "../lib/askFlow.js";
import { RULE_TYPES } from "../lib/macro.js";
import "./AskParrotDialog.css";

const PCT = (v) => `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;
const RECENT_KEY = "ggparrot.ask.recentSymbols";

function readRecent() {
  try { return JSON.parse(localStorage.getItem(RECENT_KEY) || "[]").filter(Boolean).slice(0, 5); } catch { return []; }
}
function writeRecent(symbols) {
  try { localStorage.setItem(RECENT_KEY, JSON.stringify([...new Set([...symbols, ...readRecent()])].slice(0, 5))); } catch { /* 저장 못 해도 동작 */ }
}

// 결과 카드의 핵심 설정 3줄 — 파라미터를 사람이 읽는 문장으로.
function settingLines(macro) {
  const p = macro.params || {};
  const r = macro.risk || {};
  const lines = [];
  switch (macro.rule_type) {
    case "A": lines.push(`익절 +${p.take_profit_pct}%`, r.stop_loss_pct != null ? `손절 -${r.stop_loss_pct}%` : "손절 없음"); break;
    case "C": lines.push(`${p.interval_days}일마다 ${Number(p.amount_per_buy).toLocaleString()}원씩 매수`); break;
    case "E": lines.push(`+${p.activation_profit}% 뒤 트레일링 시작`, `고점 대비 -${p.trail_percent}% 에 청산`); break;
    case "F": lines.push(`RSI(${p.rsi_period}) ${p.entry_threshold} 아래 매수`, `${p.exit_threshold} 위 매도`); break;
    case "G": lines.push(`볼린저(${p.bb_period}, ${p.bb_std}σ) 하단 매수`, p.exit_target === "mid" ? "중심선 매도" : "상단 매도"); break;
    case "H": lines.push(`기본 ${Number(p.base_order_size).toLocaleString()} + 세이프티 ${p.max_safety_orders}회`, `평단 +${p.take_profit}% 익절`); break;
    case "I": lines.push(`변동성 돌파 k=${p.k}`, p.exit_mode === "next_open" ? "다음 봉 시가 청산" : `청산: ${p.exit_mode}`); break;
    case "J": lines.push(`${p.ma_type} ${p.fast_period}/${p.slow_period} 골든크로스 매수`, "데드크로스 매도"); break;
    default: break;
  }
  lines.push(`${macro.candle_interval} 봉 · ${macro.market === "futures" ? `선물 ${macro.leverage}x` : "현물"}`);
  return lines.slice(0, 3);
}

function Chips({ options, value, onPick }) {
  return (
    <div className="ask-chips" role="group">
      {options.map((opt) => (
        <button key={opt.value} type="button" className="ask-chip t-small" aria-pressed={value === opt.value} onClick={() => onPick(opt.value)}>
          {opt.label}{opt.hint ? <span className="ask-chip-hint">{opt.hint}</span> : null}
        </button>
      ))}
    </div>
  );
}

function SymbolsCard({ answers, dispatch }) {
  const [query, setQuery] = useState("");
  const recent = readRecent().filter((s) => !POPULAR_SYMBOLS.includes(s));
  const add = () => {
    const sym = query.trim().toUpperCase().replace(/[^A-Z0-9]/g, "");
    if (!sym) return;
    dispatch({ type: "toggleSymbol", symbol: sym.endsWith("USDT") ? sym : `${sym}USDT` });
    setQuery("");
  };
  const chips = [...recent, ...POPULAR_SYMBOLS, ...answers.symbols.filter((s) => !recent.includes(s) && !POPULAR_SYMBOLS.includes(s))];
  const full = answers.symbols.length >= MAX_SYMBOLS;
  return (
    <>
      <div className="ask-chips">
        {chips.map((sym) => (
          <button key={sym} type="button" className="ask-chip t-small" aria-pressed={answers.symbols.includes(sym)}
            disabled={!answers.symbols.includes(sym) && full}
            onClick={() => dispatch({ type: "toggleSymbol", symbol: sym })}>{sym.replace(/USDT$/, "")}</button>
        ))}
      </div>
      <div className="ask-symbol-search">
        <input className="input" value={query} placeholder="종목 검색 (예: AVAX)" aria-label="종목 검색" disabled={full}
          onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
        <button type="button" className="btn btn-s btn-secondary" onClick={add} disabled={full}>추가</button>
        <button type="button" className="btn btn-s btn-primary" disabled={answers.symbols.length === 0}
          onClick={() => dispatch({ type: "confirmSymbols" })}>이 종목으로</button>
      </div>
    </>
  );
}

function StepCard({ step, answers, dispatch }) {
  if (step === "profile") return <Chips options={PROFILES} value={answers.profile} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  if (step === "market") {
    const futuresOk = canChooseFutures(answers);
    return (
      <div className="ask-chips">
        <button type="button" className="ask-chip t-small" onClick={() => dispatch({ type: "choose", step, value: { market: "spot", leverage: 1 } })}>{MARKETS[0].label}</button>
        {LEVERAGES.map((lev) => (
          <button key={lev} type="button" className="ask-chip t-small" disabled={!futuresOk} title={futuresOk ? undefined : STABLE_NO_FUTURES}
            onClick={() => dispatch({ type: "choose", step, value: { market: "futures", leverage: lev } })}>{MARKETS[1].label} {lev}x</button>
        ))}
        {!futuresOk ? <span className="t-caption text-slate-500">{STABLE_NO_FUTURES}</span> : null}
      </div>
    );
  }
  if (step === "symbols") return <SymbolsCard answers={answers} dispatch={dispatch} />;
  if (step === "period") return <Chips options={PERIODS} value={answers.period} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  if (step === "interval") return <Chips options={INTERVALS} value={answers.interval} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  return null;
}

function ResultCard({ item, onLoad }) {
  const m = item.metrics;
  return (
    <article className="ask-card" aria-label={item.label}>
      <div className="ask-card-head">
        <strong className="t-title">{RULE_TYPES[item.rule_type]?.label ?? item.label}</strong>
        <span className="t-caption text-slate-500">{item.label}</span>
      </div>
      <dl className="ask-card-metrics">
        <div><dt>수익률</dt><dd className="num">{PCT(m.final_return_pct)}</dd></div>
        <div><dt>최대 낙폭</dt><dd className="num">-{Number(m.mdd_pct).toFixed(2)}%</dd></div>
        <div><dt>승률</dt><dd className="num">{Number(m.win_rate_pct).toFixed(0)}%</dd></div>
        <div><dt>거래</dt><dd className="num">{m.total_trades}회</dd></div>
      </dl>
      <ul className="ask-card-settings">{settingLines(item.macro).map((line) => <li key={line}>{line}</li>)}</ul>
      {item.explanation ? (
        <div className="ask-card-why">
          <strong>왜 이 조합?</strong> <span className="badge badge-flat">{item.ai_generated ? "AI 생성" : "규칙 기반"}</span>
          <p className="mt-1">{item.explanation.headline}</p>
          <ul className="ask-card-settings">{(item.explanation.points || []).slice(0, 3).map((pt) => <li key={pt}>{pt}</li>)}</ul>
        </div>
      ) : null}
      <div className="ask-card-actions">
        <button type="button" className="btn btn-s btn-primary" onClick={() => onLoad(item.macro, item.label)}>조건 판에 불러오기</button>
      </div>
    </article>
  );
}

export default function AskParrotDialog({ open, onClose, onLoad }) {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [status, setStatus] = useState(null); // {consented, remaining_today, daily_limit} | {error:true}
  const [consentBusy, setConsentBusy] = useState(false);
  const titleId = useId();
  const threadRef = useRef(null);

  // 열 때 동의 상태·남은 횟수를 읽는다. 닫으면 상태를 버린다.
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    api.askStatus().then((s) => alive && setStatus(s)).catch(() => alive && setStatus({ error: true }));
    return () => { alive = false; setStatus(null); dispatch({ type: "followUp", kind: "restart" }); };
  }, [open]);

  // 카드를 다 답하면 서버에 묻는다.
  // "ready" 단계도 RUNNING_TEXT 를 그리므로(아래 렌더 참고) 여기서 "loading" 을 dispatch 하지 않는다 —
  // dispatch 하면 state.phase 가 바뀌어 이 effect 의 의존성이 바뀌고, effect 가 스스로를 정리(alive=false)해
  // 방금 시작한 요청의 결과를 영영 버리게 된다.
  useEffect(() => {
    if (state.phase !== "ready") return undefined;
    let alive = true;
    writeRecent(state.answers.symbols);
    api.askMacros(toRequest(state.answers))
      .then((data) => {
        if (!alive) return;
        dispatch({ type: "results", results: data.results, remaining: data.remaining_today });
        setStatus((s) => (s && !s.error ? { ...s, remaining_today: data.remaining_today } : s));
      })
      .catch((err) => { if (alive) dispatch({ type: "error", message: err?.message || "잠시 뒤 다시 물어봐 주세요." }); });
    return () => { alive = false; };
  }, [state.phase, state.answers]);

  useEffect(() => { threadRef.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" }); }, [state.step, state.phase]);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const consent = async () => {
    setConsentBusy(true);
    try {
      const r = await api.askConsent();
      setStatus((s) => ({ ...(s || {}), consented: r.version != null }));
    } catch {
      setStatus({ error: true });
    } finally {
      setConsentBusy(false);
    }
  };

  const isAnswered = (s) => (s === "symbols" ? state.answers.symbolsConfirmed === true : state.answers[s] != null);
  const answeredSteps = STEPS.filter(isAnswered).filter((s) => state.phase !== "cards" || STEPS.indexOf(s) < STEPS.indexOf(state.step));
  const noQuota = status && !status.error && status.remaining_today <= 0;

  return createPortal(
    <div className="scrim fixed inset-x-0 bottom-0 top-16 z-[80] flex items-start justify-center p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className="dialog w-full max-w-2xl my-4 sm:my-8">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 sticky top-0 bg-surface rounded-t-[20px] z-10">
          <h2 id={titleId} className="t-h4 text-slate-900">껄무새에게 물어볼까?</h2>
          <div className="flex items-center gap-3">
            {status && !status.error ? <span className="t-caption text-slate-500">오늘 {status.remaining_today}/{status.daily_limit}번 남음</span> : null}
            <button type="button" onClick={onClose} className="btn btn-s btn-ghost text-xl leading-none" aria-label="닫기">×</button>
          </div>
        </div>

        <div className="px-6 py-6">
          {status == null ? <p className="t-small text-slate-500">잠깐만요…</p> : status.error ? (
            <p className="t-small text-slate-700">지금은 물어볼 수 없어요. 잠시 뒤 다시 열어 주세요.</p>
          ) : !status.consented ? (
            <div className="ask-thread">
              <div className="ask-bubble ask-bubble-parrot t-small">{CONSENT_TEXT}</div>
              <div><button type="button" className="btn btn-m btn-primary" disabled={consentBusy} onClick={consent}>알겠어요</button></div>
            </div>
          ) : (
            <div className="ask-thread" ref={threadRef}>
              {answeredSteps.map((s) => (
                <div key={s} className="contents">
                  <div className="ask-bubble ask-bubble-parrot t-small">{STEP_PROMPTS[s]}</div>
                  <button type="button" className="ask-bubble ask-bubble-me t-small" title="다시 고르기" onClick={() => dispatch({ type: "back", step: s })}>{answerLabel(s, state.answers)}</button>
                </div>
              ))}

              {state.phase === "cards" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small">{STEP_PROMPTS[state.step]}</div>
                  <StepCard step={state.step} answers={state.answers} dispatch={dispatch} />
                </>
              ) : null}

              {state.phase === "loading" || state.phase === "ready" ? <div className="ask-bubble ask-bubble-parrot t-small" role="status">{RUNNING_TEXT}</div> : null}

              {state.phase === "error" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small" role="alert">{state.error}</div>
                  <div className="ask-followups"><button type="button" className="btn btn-s btn-secondary" onClick={() => dispatch({ type: "followUp", kind: "restart" })}>처음부터</button></div>
                </>
              ) : null}

              {state.phase === "results" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small">
                    {state.results.length === 0 ? NO_RESULTS_TEXT : state.results.length < 3 ? FEW_RESULTS_TEXT : "과거 데이터로 돌려 본 후보 중 상위 3개예요."}
                  </div>
                  {state.results.length ? (
                    <div className="ask-results">
                      {state.results.map((item) => <ResultCard key={item.label + item.rule_type} item={item} onLoad={onLoad} />)}
                    </div>
                  ) : null}
                  <div className="ask-disclaimer" role="note">{DISCLAIMER}</div>
                  <div className="ask-followups">
                    {FOLLOW_UPS.map((f) => (
                      <button key={f.kind} type="button" className="btn btn-s btn-secondary"
                        disabled={noQuota && f.kind !== "restart"} title={noQuota && f.kind !== "restart" ? "오늘 횟수를 다 썼어요" : undefined}
                        onClick={() => dispatch({ type: "followUp", kind: f.kind })}>{f.label}</button>
                    ))}
                  </div>
                </>
              ) : null}
            </div>
          )}
        </div>
      </div>
    </div>,
    document.body,
  );
}
