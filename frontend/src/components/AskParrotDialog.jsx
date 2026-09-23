// 껄무새에게 물어볼까? — 카드(칩) 네 장으로 답을 받아 종목 후보를 보여 주고,
// 그중 하나를 고르면 매크로 후보를 보여 준다. 결과의 숫자는 전부 서버가 계산한 값이다.
import { useEffect, useId, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import {
  CANDIDATES_PROMPT, CONSENT_TEXT, DISCLAIMER, FEW_RESULTS_TEXT, FOLLOW_UPS, HORIZONS, LEVERAGES, MANUAL_PICK_LABEL,
  MARKETS, NO_QUOTA_TEXT, NO_RESULTS_TEXT, PROFILES, RUNNING_TEXT, SCALPER_NOTE, STABLE_NO_FUTURES, STEP_PROMPTS,
  WATCH_LEVELS, feesNote,
} from "../lib/askCopy.js";
import {
  PROFILE_ORDER, STEPS, answerLabel, canChooseFutures, extraOffer, initialState, reduce, toAskRequest,
  toCandidatesRequest,
} from "../lib/askFlow.js";
import { RULE_TYPES } from "../lib/macro.js";
import "./AskParrotDialog.css";

// 제목·버튼에 쓰는 껄무새 — 에이전트 표정 중 '호기심'(brand/README.md).
const ASK_MASCOT = "/brand/agent/ggparrot-agent-curious-v1.svg";
const PCT = (v) => `${Number(v) >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`;

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

function Chips({ options, value, onPick, label }) {
  return (
    <div className="ask-chips" role="group" aria-label={label}>
      {options.map((opt) => (
        <button key={opt.value} type="button" className="ask-chip t-small" aria-pressed={value === opt.value}
          disabled={!!opt.disabled} title={opt.title} onClick={() => onPick(opt.value)}>
          {opt.label}{opt.hint ? <span className="ask-chip-hint">{opt.hint}</span> : null}
        </button>
      ))}
      {options.some((opt) => opt.disabled && opt.title) ? <span className="t-caption text-slate-500">{options.find((opt) => opt.disabled && opt.title).title}</span> : null}
    </div>
  );
}

function StepCard({ step, answers, dispatch }) {
  if (step === "profile") return <Chips options={PROFILES} value={answers.profile} label={STEP_PROMPTS[step]} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
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
  if (step === "horizon") return <Chips options={HORIZONS} value={answers.horizon} label={STEP_PROMPTS[step]} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  if (step === "watch") return <Chips options={WATCH_LEVELS} value={answers.watch} label={STEP_PROMPTS[step]} onPick={(v) => dispatch({ type: "choose", step, value: v })} />;
  return null;
}

function CandidateCard({ c, onPick }) {
  return (
    <button type="button" className="ask-cand" onClick={() => onPick(c.symbol)}>
      <span className="ask-cand-head">
        <strong>{c.base}</strong>
        <span className="ask-cand-stats num">
          거래대금 {c.volume_rank}위 · 하루 변동 {c.range_pct}%
        </span>
      </span>
      <span className="ask-cand-reason">{c.reason}</span>
    </button>
  );
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
      <p className="t-caption text-slate-500">{feesNote(item.macro?.fees?.commission_pct ?? 0.1, item.macro?.fees?.slippage_pct ?? 0.05)}</p>
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
  const [extraBusy, setExtraBusy] = useState(false);
  const [extraError, setExtraError] = useState("");
  const titleId = useId();
  const threadRef = useRef(null);
  // 서버에 요청이 나가 있는 동안은 취소할 수 없다.
  const busy = state.phase === "loadingCandidates" || state.phase === "loadingResults";

  // 열 때 동의 상태·남은 횟수를 읽는다. 닫으면 상태를 버린다.
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    api.askStatus().then((s) => alive && setStatus(s)).catch(() => alive && setStatus({ error: true }));
    return () => { alive = false; setStatus(null); dispatch({ type: "followUp", kind: "restart" }); };
  }, [open]);

  useEffect(() => { threadRef.current?.lastElementChild?.scrollIntoView?.({ block: "nearest" }); }, [state.step, state.phase]);
  useEffect(() => {
    if (!open) return undefined;
    // 요청이 도는 중에는 Esc 로도 닫지 못한다(중간 취소 금지).
    const onKey = (e) => { if (e.key === "Escape" && !busy) onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, busy]);

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

  // 포인트로 1번 더 — 서버가 잔액·상한을 다시 검사하고, 성공하면 남은 횟수와 잔액을 그대로 돌려준다.
  const buyExtra = async () => {
    setExtraBusy(true);
    setExtraError("");
    try {
      const r = await api.askExtra();
      setStatus((s) => (s && !s.error ? { ...s, remaining_today: r.remaining_today, extra_left_today: r.extra_left_today, points_balance: r.points_balance } : s));
    } catch (err) {
      setExtraError(err?.message || "지금은 추가할 수 없어요. 잠시 뒤 다시 시도해 주세요.");
    } finally {
      setExtraBusy(false);
    }
  };

  // 카드 네 장에 다 답하면(phase "ready") 이 버튼으로 종목 후보를 받는다 — 하루 횟수는 여기서 차감된다.
  const submitCards = async () => {
    dispatch({ type: "loadingCandidates" });
    try {
      const data = await api.askCandidates(toCandidatesRequest(state.answers));
      dispatch({
        type: "candidates",
        session: { id: data.session_id, remaining: data.remaining_today },
        candidates: data.candidates,
        manualSymbols: data.manual_symbols,
      });
      setStatus((s) => (s && !s.error ? { ...s, remaining_today: data.remaining_today } : s));
    } catch (e) {
      dispatch({ type: "error", message: e.message });
    }
  };

  // 후보(또는 직접 고르기)에서 종목을 하나 고르면 바로 매크로 후보를 받는다 — 이 요청은 차감 없음.
  const pickSymbol = async (symbol) => {
    const next = reduce(state, { type: "chooseSymbol", symbol });
    if (!next.answers.symbol) return;
    dispatch({ type: "chooseSymbol", symbol });
    dispatch({ type: "loadingResults" });
    try {
      const data = await api.askMacros(toAskRequest(next));
      dispatch({ type: "results", results: data.results, remaining: data.remaining_today });
    } catch (e) {
      dispatch({ type: "error", message: e.message });
    }
  };

  const isAnswered = (s) => state.answers[s] != null;
  const answeredSteps = STEPS.filter(isAnswered).filter((s) => state.phase !== "cards" || STEPS.indexOf(s) < STEPS.indexOf(state.step));
  const noQuota = status && !status.error && status.remaining_today <= 0;
  const offer = extraOffer(status);
  const extraBlock = offer.show ? (
    <div className="ask-extra" role="group" aria-label="포인트로 횟수 추가">
      <button type="button" className="btn btn-s btn-primary" disabled={!offer.canBuy || extraBusy} onClick={buyExtra}>
        {extraBusy ? "추가하는 중…" : offer.label}
      </button>
      <span className="t-caption text-slate-500">{extraError || offer.note}</span>
    </div>
  ) : null;

  // 성향을 바꾸는 두 버튼(안전하게/공격적으로)은 세션을 버리고 새로 후보를 받으므로 하루 횟수가 하나 더 든다.
  const followLabel = (f) => {
    if (f.kind === "safer") return `안전하게 (횟수 1회 더 써요 · 남은 ${state.remaining}회)`;
    if (f.kind === "riskier") return `공격적으로 (횟수 1회 더 써요 · 남은 ${state.remaining}회)`;
    return f.label;
  };

  return createPortal(
    <div className="scrim fixed inset-x-0 bottom-0 top-16 z-[80] flex items-start justify-center p-2 sm:p-4 overflow-y-auto">
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className="dialog w-full max-w-2xl my-4 sm:my-8">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 sticky top-0 bg-surface rounded-t-[20px] z-10">
          <h2 id={titleId} className="t-h4 text-slate-900 ask-title">
            <img src={ASK_MASCOT} alt="" width="100" height="100" className="ask-title-face" aria-hidden="true" />
            껄무새에게 물어볼까?
          </h2>
          <div className="flex items-center gap-3">
            {status && !status.error ? <span className="t-caption text-slate-500">오늘 {status.remaining_today}/{status.daily_limit}번 남음</span> : null}
            <button type="button" onClick={onClose} disabled={busy} className="btn btn-s btn-ghost text-xl leading-none" aria-label="닫기">×</button>
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
                  <button type="button" className="ask-bubble ask-bubble-me t-small" title="다시 고르기" disabled={busy} onClick={() => dispatch({ type: "back", step: s })}>{answerLabel(s, state.answers)}</button>
                </div>
              ))}

              {state.phase === "cards" ? (
                noQuota && answeredSteps.length === 0 ? (
                  <>
                    <div className="ask-bubble ask-bubble-parrot t-small" role="status">{NO_QUOTA_TEXT}</div>
                    {extraBlock}
                  </>
                ) : (
                  <>
                    <div className="ask-bubble ask-bubble-parrot t-small">{STEP_PROMPTS[state.step]}</div>
                    <StepCard step={state.step} answers={state.answers} dispatch={dispatch} />
                  </>
                )
              ) : null}

              {state.phase === "ready" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small">네 가지 답 다 됐어요. 성향에 맞는 종목 후보를 볼까요?</div>
                  <div><button type="button" className="btn btn-m btn-primary" onClick={submitCards}>후보 보기</button></div>
                </>
              ) : null}

              {state.phase === "loadingCandidates" || state.phase === "loadingResults" ? (
                <div className="ask-bubble ask-bubble-parrot t-small" role="status">{RUNNING_TEXT}</div>
              ) : null}

              {state.phase === "error" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small" role="alert">{state.error}</div>
                  <div className="ask-followups"><button type="button" className="btn btn-s btn-secondary" onClick={() => dispatch({ type: "followUp", kind: "restart" })}>처음부터</button></div>
                </>
              ) : null}

              {state.phase === "candidates" ? (
                <>
                  <div className="ask-bubble ask-bubble-parrot t-small">{CANDIDATES_PROMPT}</div>
                  {state.candidates.length ? (
                    <div className="ask-cand-list">
                      {state.candidates.map((c) => <CandidateCard key={c.symbol} c={c} onPick={pickSymbol} />)}
                    </div>
                  ) : null}
                  {state.manualSymbols.length ? (
                    <details>
                      <summary className="t-small text-slate-500">{MANUAL_PICK_LABEL}</summary>
                      <div className="ask-chips">
                        {state.manualSymbols.map((sym) => (
                          <button key={sym} type="button" className="ask-chip t-small" onClick={() => pickSymbol(sym)}>{sym.replace(/USDT$/, "")}</button>
                        ))}
                      </div>
                    </details>
                  ) : null}
                  <div className="ask-disclaimer" role="note">{DISCLAIMER}</div>
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
                  {state.answers.profile === "scalper" ? <div className="ask-disclaimer" role="note">{SCALPER_NOTE}</div> : null}
                  {extraBlock}
                  <div className="ask-followups">
                    {FOLLOW_UPS.map((f) => {
                      const quotaBlocked = noQuota && (f.kind === "safer" || f.kind === "riskier");
                      // PROFILE_ORDER 양쪽 끝(안정형 "안전하게", 단타형 "공격적으로")은 갈 곳이 없다.
                      const idx = PROFILE_ORDER.indexOf(state.answers.profile);
                      const edgeBlocked = (f.kind === "safer" && idx <= 0)
                        || (f.kind === "riskier" && idx >= PROFILE_ORDER.length - 1);
                      const disabled = quotaBlocked || edgeBlocked;
                      const title = quotaBlocked ? "오늘 횟수를 다 썼어요" : edgeBlocked ? "이미 그쪽 끝이에요" : undefined;
                      return (
                        <button key={f.kind} type="button" className="btn btn-s btn-secondary"
                          disabled={disabled} title={title}
                          onClick={() => dispatch({ type: "followUp", kind: f.kind })}>{followLabel(f)}</button>
                      );
                    })}
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
