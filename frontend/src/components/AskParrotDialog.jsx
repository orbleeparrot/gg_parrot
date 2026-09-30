// 껄무새에게 물어볼까? (2026-09-30 재설계) — 가운데 카드에 질문을 하나씩(B), 종목·매크로 후보는
// 매크로 카드 짜임의 가로 카드로(E), 휴대폰에서는 매크로 후보를 좌우 화살표로 한 장씩 넘긴다(F).
// 흐름(askFlow 리듀서)과 서버 호출은 그대로이고, 결과의 숫자는 전부 서버가 계산한 값이다.
// 움직임은 토스 모션 값(120 눌림 · 200 전환 · 320 등장, 튕김 없음)만 쓴다 — AskParrotDialog.css.
import { Fragment, useCallback, useEffect, useId, useReducer, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "../api.js";
import CoinIcon from "./CoinIcon.jsx";
import {
  BACK_TO_STEP, CANDIDATES_PROMPT, COMPARE_TITLE, CONDITION_LABEL, CONSENT_BUTTON, CONSENT_TEXT, DISCLAIMER,
  FOLLOW_UPS, FOLLOW_UPS_TITLE, HOLD_LABEL, HORIZONS, LEGEND_BASE, LEGEND_EQUITY, LEVERAGES, LOAD_BUTTON,
  LOST_TO_HOLD_TEXT, MANUAL_PICK_LABEL, MANUAL_SEARCH_MISS, MANUAL_SEARCH_PLACEHOLDER, MARKETS, NEXT_LABEL,
  NO_QUOTA_TEXT, OPENING_TEXT, PREV_LABEL, PROFILES, READY_BUTTON, READY_TEXT, RESTART_LABEL, RETURN_LABEL,
  RUNNING_TEXT, SCALPER_NOTE, SPOT_HINT, STABLE_NO_FUTURES, STEP_PROMPTS, UNAVAILABLE_TEXT, VS_HOLD_LABEL,
  WATCH_LEVELS, WHY_TITLE, feesNote, readyCostNote,
} from "../lib/askCopy.js";
import { useSymbolList } from "../hooks/useSymbolList.js";
import { resolveSymbol, searchSymbols } from "../lib/symbolSearch.js";
import {
  PROFILE_ORDER, STEPS, answerLabel, canChooseFutures, extraOffer, initialState, reduce, toAskRequest,
  toCandidatesRequest,
} from "../lib/askFlow.js";
import {
  coinTint, conditionLines, holdDiff, holdOf, intervalLabel, marketLabel, numberParts, resultsHeadline,
  signedPct, signedPoints, sparkPaths, splitRuleLabel, whyHeadline, whyPoints,
} from "../lib/askView.js";
import { coinName } from "../lib/macroSource.js";
import { baseOf, quoteOf } from "../lib/format.js";
import { RULE_TYPES } from "../lib/macro.js";
import "./AskParrotDialog.css";

// 껄무새 얼굴 — 에이전트 표정 중 '호기심'(brand/README.md).
const ASK_MASCOT = "/brand/agent/ggparrot-agent-curious-v1.svg";
const NARROW_QUERY = "(max-width: 640px)";

function useMedia(query) {
  const get = () => (typeof window !== "undefined" && window.matchMedia ? window.matchMedia(query).matches : false);
  const [matches, setMatches] = useState(get);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return undefined;
    const mq = window.matchMedia(query);
    const on = () => setMatches(mq.matches);
    on();
    mq.addEventListener?.("change", on);
    return () => mq.removeEventListener?.("change", on);
  }, [query]);
  return matches;
}

// 문장 속 숫자를 고정폭 굵게 — 매크로 카드의 조건 문장과 같은 규칙.
function Nums({ text }) {
  return numberParts(text).map((part, i) => (part.t === "num" ? <b key={i} className="num">{part.v}</b> : <Fragment key={i}>{part.v}</Fragment>));
}

function Face({ size = 56 }) {
  return <img src={ASK_MASCOT} alt="" width={size} height={size} className="ask-face" style={{ width: size, height: size }} aria-hidden="true" />;
}

function Dots() {
  return <span className="ask-dots3" aria-hidden="true"><i /><i /><i /></span>;
}

const inStyle = (i, extra) => ({ "--i": i, ...extra });

// 질문 한 장의 선택지.
function stepOptions(step, answers) {
  if (step === "profile") return PROFILES.map((p) => ({ key: p.value, label: p.label, hint: p.hint, value: p.value }));
  if (step === "market") {
    const ok = canChooseFutures(answers);
    return [
      { key: "spot", label: MARKETS[0].label, hint: SPOT_HINT, value: { market: "spot", leverage: 1 } },
      ...LEVERAGES.map((lev) => ({ key: `futures-${lev}`, label: `${MARKETS[1].label} ${lev}x`, hint: "", value: { market: "futures", leverage: lev }, disabled: !ok, title: ok ? undefined : STABLE_NO_FUTURES })),
    ];
  }
  if (step === "horizon") return HORIZONS.map((h) => ({ key: h.value, label: h.label, hint: h.hint, value: h.value }));
  if (step === "watch") return WATCH_LEVELS.map((w) => ({ key: w.value, label: w.label, hint: w.hint, value: w.value }));
  return [];
}

function Spark({ item }) {
  const paths = sparkPaths(item.curve, item.initial_capital);
  if (!paths) return null;
  return (
    <svg className={"ask-spark " + (paths.up ? "is-up" : "is-down")} viewBox="0 0 100 44" preserveAspectRatio="none" aria-hidden="true">
      <path className="ask-spark-a" d={paths.area} />
      <path className="ask-spark-b" d={paths.base} vectorEffect="non-scaling-stroke" />
      <path className="ask-spark-l" d={paths.line} vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

// 휴대폰 카드에서는 성과 칸(홀딩 대비 · MDD · 승률)과 같은 숫자를 되풀이하는 줄은 빼고, 그 밖의 이유만 남긴다.
const SAME_AS_STATS = new Set(["홀딩 대비", "최대 낙폭", "승률"]);

function Why({ item, compact = false }) {
  const why = item.explanation;
  if (!why) return null;
  const points = whyPoints(why.points).filter((pt) => !compact || !SAME_AS_STATS.has(pt.label));
  return (
    <section className="ask-why" aria-label={WHY_TITLE}>
      <div className="ask-why-hd">
        <span className="ask-why-av"><img src={ASK_MASCOT} alt="" width="34" height="34" aria-hidden="true" /></span>
        <div className="ask-why-head">
          <p className="ask-why-eb">{WHY_TITLE} <span className={"badge " + (item.ai_generated ? "badge-ai" : "badge-flat")}>{item.ai_generated ? "AI 생성" : "규칙 기반"}</span></p>
          <p className="ask-why-h">{whyHeadline(why.headline)}</p>
        </div>
      </div>
      {points.length ? (
        <ul className="ask-why-pts">
          {points.map((pt) => (
            <li key={pt.text}>
              <span className="ask-why-fig num">{pt.fig}</span>
              <span className="ask-lbl">{pt.label}</span>
              <span className="ask-why-txt"><Nums text={pt.text} /></span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

// 매크로 후보 카드 — 머리 한 줄(순위 · 종목 · 매매 방식 · 시장 · 불러오기) + [성과·조건 | 왜 이 조합?].
function ResultCard({ item, rank, best, compact = false, onLoad }) {
  const macro = item.macro || {};
  const symbols = Array.isArray(macro.symbols) && macro.symbols.length > 1 ? macro.symbols : [macro.symbol].filter(Boolean);
  const first = symbols[0] || "";
  const { name, by } = splitRuleLabel(RULE_TYPES[item.rule_type]?.label ?? item.label);
  const m = item.metrics || {};
  const ret = Number(m.final_return_pct);
  const diff = holdDiff(item);
  const lines = conditionLines(macro);
  return (
    <article className={"ask-mc" + (best ? " is-best" : "") + (compact ? " is-compact" : "")} style={{ "--coin": coinTint(baseOf(first)) || "transparent" }} aria-label={`${rank}위 · ${name}`}>
      <header className="ask-mc-head">
        <span className={`ask-rank is-${rank}`}><b className="num">{rank}</b>위</span>
        <CoinIcon symbol={first} size={32} alt="" className="ask-logo" />
        <span className="ask-mc-id">
          <span className="ask-tk num"><strong>{symbols.length > 1 ? symbols.map(baseOf).join(" · ") : baseOf(first)}</strong><small>{quoteOf(first)}</small></span>
          <span className="ask-mc-rule">{name}{by ? <em> · {by}</em> : null}</span>
        </span>
        <span className="ask-mc-tags">
          <span className="ask-tag">{marketLabel(macro)}</span>
          {intervalLabel(macro) ? <span className="ask-tag num">{intervalLabel(macro)}</span> : null}
        </span>
        {compact ? null : (
          <button type="button" className={"btn btn-s ask-mc-load " + (best ? "btn-primary" : "btn-secondary")} onClick={() => onLoad(item.macro, item.label)}>{LOAD_BUTTON}</button>
        )}
      </header>
      <div className="ask-mc-body">
        <div className="ask-mc-perf">
          <div className="ask-hero">
            <div><span className="ask-lbl">{RETURN_LABEL}</span><strong className={"num " + (ret >= 0 ? "is-up" : "is-down")}>{signedPct(ret)}</strong></div>
            <Spark item={item} />
          </div>
          <dl className="ask-stats">
            <div><dt><span className="is-long">{VS_HOLD_LABEL}</span><span className="is-short">홀딩 대비</span></dt><dd className={"num " + (diff == null ? "" : diff >= 0 ? "is-up" : "is-down")}>{signedPoints(diff)}</dd></div>
            <div><dt>MDD</dt><dd className="num is-down">-{Number(m.mdd_pct).toFixed(2)}%</dd></div>
            <div><dt>승률</dt><dd className="num">{Number(m.win_rate_pct).toFixed(0)}%</dd></div>
            <div><dt>매매</dt><dd><span className="num">{m.total_trades}</span>회</dd></div>
          </dl>
          <div className="ask-cond">
            <span className="ask-lbl">{CONDITION_LABEL}</span>
            <span className="ask-cond-v">{lines.map((line, i) => <Fragment key={line}>{i ? <i aria-hidden="true">|</i> : null}<Nums text={line} /></Fragment>)}</span>
            <span className="ask-fee">{feesNote(macro.fees?.commission_pct ?? 0.1, macro.fees?.slippage_pct ?? 0.05)}</span>
          </div>
        </div>
        <Why item={item} compact={compact} />
      </div>
    </article>
  );
}

// 종목 후보 — 한 줄 카드: 로고 · 티커 · 이유 · 거래대금 순위 · 하루 변동.
function CandidateRow({ c, marketText, onPick, disabled, i }) {
  const kr = coinName(c.symbol);
  return (
    <button type="button" className="ask-cand ask-in" style={inStyle(i, { "--coin": coinTint(c.base) || "transparent" })} onClick={() => onPick(c.symbol)} disabled={disabled}>
      <CoinIcon symbol={c.symbol} size={32} alt="" className="ask-logo" />
      <span className="ask-cand-name">
        <span className="ask-tk num"><strong>{c.base}</strong><small>{quoteOf(c.symbol)}</small></span>
        <span className="ask-lbl">{kr ? `${kr} · ${marketText}` : marketText}</span>
      </span>
      <span className="ask-cand-why"><Nums text={c.reason || ""} /></span>
      <span className="ask-stat is-rank"><span className="ask-lbl">거래대금</span><span className="ask-stat-v"><b className="num">{c.volume_rank}</b>위</span></span>
      <span className="ask-stat is-range"><span className="ask-lbl">하루 변동</span><span className="ask-stat-v"><b className="num">{c.range_pct}</b>%</span></span>
      <span className="ask-chev" aria-hidden="true" />
    </button>
  );
}

// 직접 고를래요 — 빠른 선택 칩 + 거래 가능 종목 검색(빌더 검색창과 같은 목록).
function ManualPick({ chips, onPick, disabled, i }) {
  const [query, setQuery] = useState("");
  const [note, setNote] = useState("");
  const { items } = useSymbolList();
  const q = query.trim();
  const matches = items && q ? searchSymbols(items, q, { limit: 8 }) : [];
  const add = () => {
    if (!q) return;
    const resolved = items ? resolveSymbol(items, q) : null;
    if (!resolved) { setNote(MANUAL_SEARCH_MISS); return; }
    setQuery("");
    setNote("");
    onPick(resolved, true);
  };
  return (
    <section className="ask-manual ask-in" style={inStyle(i)} aria-label={MANUAL_PICK_LABEL}>
      <h4 className="ask-manual-t">{MANUAL_PICK_LABEL}</h4>
      {chips.length ? (
        <div className="ask-chips">
          {chips.map((sym) => (
            <button key={sym} type="button" className="ask-chip num" disabled={disabled} onClick={() => onPick(sym)}>
              <CoinIcon symbol={sym} size={16} alt="" className="ask-logo" />{baseOf(sym)}
            </button>
          ))}
        </div>
      ) : null}
      <div className="ask-search">
        <input
          className="input"
          value={query}
          placeholder={MANUAL_SEARCH_PLACEHOLDER}
          aria-label="종목 검색"
          onChange={(e) => { setQuery(e.target.value); setNote(""); }}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }}
        />
        <button type="button" className="btn btn-s btn-secondary" disabled={disabled} onClick={add}>고르기</button>
      </div>
      {matches.length ? (
        <div className="ask-chips">
          {matches.map((item) => (
            <button key={item.symbol} type="button" className="ask-chip num" disabled={disabled}
              onClick={() => { setQuery(""); setNote(""); onPick(item.symbol, true); }}>
              <CoinIcon symbol={item.symbol} size={16} alt="" className="ask-logo" />{baseOf(item.symbol)}
            </button>
          ))}
        </div>
      ) : null}
      {note ? <p className="ask-miss" role="status">{note}</p> : null}
    </section>
  );
}

function CompareTable({ results, hold }) {
  return (
    <div className="ask-sum ask-in" style={inStyle(2)}>
      <p className="ask-sum-t">{COMPARE_TITLE}</p>
      <table>
        <thead><tr><th scope="col">순위</th><th scope="col">조합</th><th scope="col">수익률</th><th scope="col" className="is-mdd">MDD</th><th scope="col">{VS_HOLD_LABEL}</th></tr></thead>
        <tbody>
          {results.map((item, idx) => {
            const ret = Number(item.metrics?.final_return_pct);
            const diff = holdDiff(item);
            return (
              <tr key={item.label + item.rule_type}>
                <td><span className={`ask-rank is-${idx + 1}`}><b className="num">{idx + 1}</b>위</span></td>
                <td className="is-name">{splitRuleLabel(RULE_TYPES[item.rule_type]?.label ?? item.label).name}</td>
                <td className={"num " + (ret >= 0 ? "is-up" : "is-down")}>{signedPct(ret)}</td>
                <td className="num is-down is-mdd">-{Number(item.metrics?.mdd_pct).toFixed(2)}%</td>
                <td className={"num " + (diff == null ? "" : diff >= 0 ? "is-up" : "is-down")}>{signedPoints(diff)}</td>
              </tr>
            );
          })}
        </tbody>
        {hold != null ? (
          <tfoot><tr><td /><td className="is-name">{HOLD_LABEL}</td><td className="num">{signedPct(hold, 1)}</td><td className="num is-mdd">—</td><td>기준</td></tr></tfoot>
        ) : null}
      </table>
    </div>
  );
}

export default function AskParrotDialog({ open, onClose, onLoad }) {
  const [state, dispatch] = useReducer(reduce, undefined, initialState);
  const [status, setStatus] = useState(null); // {consented, remaining_today, daily_limit} | {error:true}
  const [consentBusy, setConsentBusy] = useState(false);
  const [extraBusy, setExtraBusy] = useState(false);
  const [extraError, setExtraError] = useState("");
  const [leaving, setLeaving] = useState(false); // 다음 장으로 넘어가기 전 퇴장 중
  const [picked, setPicked] = useState(null); // 방금 고른 선택지(체크 표시 뒤 넘어간다)
  const [active, setActive] = useState(0); // 휴대폰에서 지금 보고 있는 카드
  const narrow = useMedia(NARROW_QUERY);
  const reducedMotion = useMedia("(prefers-reduced-motion: reduce)");
  const titleId = useId();
  const bodyRef = useRef(null);
  const timers = useRef([]);
  const trackRef = useRef(null);
  // 서버에 요청이 나가 있는 동안은 취소할 수 없다.
  const busy = state.phase === "loadingCandidates" || state.phase === "loadingResults";
  const lock = busy || leaving || picked != null;
  const T = reducedMotion ? { pick: 0, out: 0 } : { pick: 260, out: 240 };

  const later = useCallback((ms, fn) => {
    if (!ms) { fn(); return; }
    timers.current.push(setTimeout(fn, ms));
  }, []);
  const leaveThen = useCallback((fn) => {
    if (!T.out) { fn(); return; }
    setLeaving(true);
    later(T.out, () => { setLeaving(false); fn(); });
  }, [T.out, later]);

  // 열 때 동의 상태·남은 횟수를 읽는다. 닫으면 상태를 버린다.
  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    api.askStatus().then((s) => alive && setStatus(s)).catch(() => alive && setStatus({ error: true }));
    return () => {
      alive = false;
      setStatus(null);
      dispatch({ type: "followUp", kind: "restart" });
      timers.current.forEach(clearTimeout);
      timers.current = [];
      setLeaving(false);
      setPicked(null);
    };
  }, [open]);

  const stageKey = `${status ? (status.error ? "e" : status.consented ? "c" : "n") : "-"}:${state.phase}:${state.step}:${state.answers.symbol || ""}`;
  useEffect(() => { setPicked(null); bodyRef.current?.scrollTo?.({ top: 0 }); }, [stageKey]);
  useEffect(() => { setActive(0); trackRef.current?.scrollTo?.({ left: 0 }); }, [state.results]);

  const noQuota = status && !status.error && status.remaining_today <= 0;
  const answeredSteps = STEPS.filter((s) => state.answers[s] != null)
    .filter((s) => state.phase !== "cards" || STEPS.indexOf(s) < STEPS.indexOf(state.step));
  const asking = status && !status.error && status.consented && (state.phase === "cards" || state.phase === "ready") && !(noQuota && answeredSteps.length === 0);
  const options = asking && state.phase === "cards" ? stepOptions(state.step, state.answers) : [];
  const results = state.phase === "results" ? state.results || [] : [];
  const carousel = narrow && results.length > 0;
  const current = carousel ? results[Math.min(active, results.length - 1)] : null;

  const choose = (opt, index) => {
    if (lock || opt.disabled) return;
    setPicked(index);
    later(T.pick, () => leaveThen(() => dispatch({ type: "choose", step: state.step, value: opt.value })));
  };
  // 휴대폰 카드 — 가로 스크롤 스냅. 손가락으로 밀면 한 장씩 멈추고, 점을 누르거나 ←→ 키로도 옮긴다.
  const slideTo = (i) => {
    const track = trackRef.current;
    const el = track?.children?.[i];
    if (!track || !el) return;
    track.scrollTo({ left: el.offsetLeft - (track.clientWidth - el.clientWidth) / 2, behavior: reducedMotion ? "auto" : "smooth" });
  };
  const go = (dir) => slideTo(Math.max(0, Math.min(results.length - 1, active + dir)));
  const onTrackScroll = () => {
    const track = trackRef.current;
    if (!track || !track.children.length) return;
    const mid = track.scrollLeft + track.clientWidth / 2;
    let best = 0;
    let dist = Infinity;
    Array.from(track.children).forEach((el, i) => {
      const d = Math.abs(el.offsetLeft + el.clientWidth / 2 - mid);
      if (d < dist) { dist = d; best = i; }
    });
    if (best !== active) setActive(best);
  };

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      // 요청이 도는 중에는 Esc 로도 닫지 못한다(중간 취소 금지).
      if (e.key === "Escape" && !busy) { onClose(); return; }
      if (e.target instanceof HTMLElement && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return;
      if (options.length && /^[1-9]$/.test(e.key)) {
        const idx = Number(e.key) - 1;
        if (options[idx]) { e.preventDefault(); choose(options[idx], idx); }
      }
      if (carousel && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); go(e.key === "ArrowLeft" ? -1 : 1); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  });

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

  // 네 답을 다 하면(phase "ready") 종목 후보를 받는다 — 하루 횟수는 여기서 차감된다.
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
  const pickSymbol = async (symbol, resolved = false) => {
    const next = reduce(state, { type: "chooseSymbol", symbol, resolved });
    if (!next.answers.symbol) return;
    dispatch({ type: "chooseSymbol", symbol, resolved });
    dispatch({ type: "loadingResults" });
    try {
      const data = await api.askMacros(toAskRequest(next));
      dispatch({ type: "results", results: data.results, lostToHold: data.all_lost_to_hold, remaining: data.remaining_today });
    } catch (e) {
      dispatch({ type: "error", message: e.message });
    }
  };

  const offer = extraOffer(status);
  const extraBlock = (i) => (offer.show ? (
    <div className="ask-extra ask-in" style={inStyle(i)} role="group" aria-label="포인트로 횟수 추가">
      <button type="button" className={"btn btn-s " + (offer.canBuy ? "btn-primary" : "btn-secondary")} disabled={!offer.canBuy || extraBusy} onClick={buyExtra}>
        {extraBusy ? "추가하는 중…" : offer.label}
      </button>
      <span className="ask-caption">{extraError || offer.note}</span>
    </div>
  ) : null);

  // 성향을 바꾸는 두 버튼(안전하게/공격적으로)은 세션을 버리고 새로 후보를 받으므로 하루 횟수가 하나 더 든다.
  const followLabel = (f) => {
    if (f.kind === "safer") return `안전하게 (횟수 1회 더 써요 · 남은 ${state.remaining}회)`;
    if (f.kind === "riskier") return `공격적으로 (횟수 1회 더 써요 · 남은 ${state.remaining}회)`;
    return f.label;
  };
  const followUps = (i, className = "") => (
    <div className={"ask-fups ask-in " + className} style={inStyle(i)} role="group" aria-label={FOLLOW_UPS_TITLE}>
      <span className="ask-fups-t">{FOLLOW_UPS_TITLE}</span>
      {FOLLOW_UPS.map((f) => {
        const quotaBlocked = noQuota && (f.kind === "safer" || f.kind === "riskier");
        // PROFILE_ORDER 양쪽 끝(안정형 "안전하게", 단타형 "공격적으로")은 갈 곳이 없다.
        const idx = PROFILE_ORDER.indexOf(state.answers.profile);
        const edgeBlocked = (f.kind === "safer" && idx <= 0) || (f.kind === "riskier" && idx >= PROFILE_ORDER.length - 1);
        const disabled = quotaBlocked || edgeBlocked || lock;
        const title = quotaBlocked ? "오늘 횟수를 다 썼어요" : edgeBlocked ? "이미 그쪽 끝이에요" : undefined;
        return (
          <button key={f.kind} type="button" className="btn btn-s btn-secondary" disabled={disabled} title={title}
            onClick={() => leaveThen(() => dispatch({ type: "followUp", kind: f.kind }))}>{followLabel(f)}</button>
        );
      })}
    </div>
  );

  const marketText = state.answers.market === "futures" ? `${MARKETS[1].label} ${state.answers.leverage}x` : MARKETS[0].label;
  const hold = holdOf(results);

  let stage;
  if (status == null) {
    stage = <div className="ask-center ask-in" style={inStyle(0)}><Dots /><p className="ask-lead">{OPENING_TEXT}</p></div>;
  } else if (status.error) {
    stage = <div className="ask-center ask-in" style={inStyle(0)}><Face size={48} /><p className="ask-lead is-strong">{UNAVAILABLE_TEXT}</p></div>;
  } else if (!status.consented) {
    stage = (
      <div className="ask-center">
        <span className="ask-in" style={inStyle(0)}><Face size={64} /></span>
        <p className="ask-lead is-strong ask-in" style={inStyle(1)}>{CONSENT_TEXT}</p>
        <button type="button" className="btn btn-m btn-primary ask-in" style={inStyle(2)} disabled={consentBusy} onClick={consent}>{CONSENT_BUTTON}</button>
      </div>
    );
  } else if (state.phase === "cards" && noQuota && answeredSteps.length === 0) {
    stage = (
      <div className="ask-center">
        <span className="ask-in" style={inStyle(0)}><Face /></span>
        <p className="ask-lead is-strong ask-in" style={inStyle(1)} role="status">{NO_QUOTA_TEXT}</p>
        {extraBlock(2)}
      </div>
    );
  } else if (state.phase === "cards") {
    const two = options.length === 4 && state.step !== "profile";
    stage = (
      <>
        <h3 className="ask-q ask-in" style={inStyle(0)}>{STEP_PROMPTS[state.step]}</h3>
        <div className={"ask-opts" + (two ? " is-two" : "")} role="group" aria-label={STEP_PROMPTS[state.step]}>
          {options.map((opt, idx) => (
            <button key={opt.key} type="button" disabled={opt.disabled} title={opt.title}
              className={"ask-opt ask-in" + (picked === idx ? " is-picked" : picked != null ? " is-dim" : "")}
              style={inStyle(idx + 1)} aria-pressed={picked === idx} onClick={() => choose(opt, idx)}>
              <span className="ask-opt-l"><span className="ask-key num" aria-hidden="true">{idx + 1}</span>{opt.label}</span>
              <span className="ask-opt-r">{opt.hint ? <small>{opt.hint}</small> : null}<span className="ask-check" aria-hidden="true">✓</span></span>
            </button>
          ))}
        </div>
        {state.step === "market" && !canChooseFutures(state.answers) ? <p className="ask-note ask-in" style={inStyle(options.length + 1)}>{STABLE_NO_FUTURES}</p> : null}
      </>
    );
  } else if (state.phase === "ready") {
    stage = (
      <div className="ask-center">
        <p className="ask-say ask-in" style={inStyle(0)}><Face size={32} /><span>{READY_TEXT}</span></p>
        <button type="button" className="btn btn-m btn-primary ask-in" style={inStyle(1)} disabled={lock} onClick={() => leaveThen(submitCards)}>{READY_BUTTON}</button>
        <span className="ask-caption ask-in" style={inStyle(2)}>{readyCostNote(status.remaining_today)}</span>
      </div>
    );
  } else if (busy) {
    stage = <div className="ask-center ask-in" style={inStyle(0)} role="status"><Dots /><p className="ask-lead is-strong">{RUNNING_TEXT}</p></div>;
  } else if (state.phase === "error") {
    stage = (
      <div className="ask-center">
        <span className="ask-in" style={inStyle(0)}><Face size={48} /></span>
        <p className="ask-lead is-strong ask-in" style={inStyle(1)} role="alert">{state.error}</p>
        <button type="button" className="btn btn-s btn-secondary ask-in" style={inStyle(2)} onClick={() => leaveThen(() => dispatch({ type: "followUp", kind: "restart" }))}>{RESTART_LABEL}</button>
      </div>
    );
  } else if (state.phase === "candidates") {
    const n = state.candidates.length;
    stage = (
      <>
        <p className="ask-say ask-in" style={inStyle(0)}><Face size={32} /><span>{CANDIDATES_PROMPT}</span></p>
        {n ? (
          <div className="ask-cands">
            {state.candidates.map((c, idx) => <CandidateRow key={c.symbol} c={c} i={idx + 1} marketText={marketText} disabled={lock} onPick={(sym) => leaveThen(() => pickSymbol(sym))} />)}
          </div>
        ) : null}
        <ManualPick chips={state.manualSymbols} i={n + 1} disabled={lock} onPick={(sym, resolved) => leaveThen(() => pickSymbol(sym, resolved))} />
        <p className="ask-disc ask-in" style={inStyle(n + 2)} role="note">{DISCLAIMER}</p>
      </>
    );
  } else if (state.phase === "results") {
    stage = (
      <>
        <div className={"ask-rh ask-in" + (carousel ? " is-compact" : "")} style={inStyle(0)}>
          <p className="ask-say"><Face size={32} /><span>{resultsHeadline(results.length)}</span></p>
          {results.length && !carousel ? <span className="ask-legend"><span><i />{LEGEND_EQUITY}</span><span><i className="is-base" />{LEGEND_BASE}</span></span> : null}
        </div>
        {state.lostToHold ? <p className="ask-hold ask-in" style={inStyle(1)} role="note">{LOST_TO_HOLD_TEXT}</p> : null}
        {results.length > 1 && !carousel ? <CompareTable results={results} hold={hold} /> : null}
        {results.length && !carousel ? (
          <div className="ask-mcs">
            {results.map((item, idx) => (
              <div key={item.label + item.rule_type} className="ask-in" style={inStyle(idx + 3)}>
                <ResultCard item={item} rank={idx + 1} best={idx === 0} onLoad={onLoad} />
              </div>
            ))}
          </div>
        ) : null}
        {carousel ? (
          <div className="ask-car ask-in" style={inStyle(2)}>
            <div className="ask-car-track" ref={trackRef} onScroll={onTrackScroll} aria-roledescription="carousel" aria-label="매크로 후보">
              {results.map((item, idx) => (
                <div key={item.label + item.rule_type} className={"ask-car-slide" + (idx === active ? " is-on" : "")}
                  aria-roledescription="slide" aria-label={`${idx + 1} / ${results.length}`} onClick={() => idx !== active && slideTo(idx)}>
                  <div className="ask-mcs"><ResultCard item={item} rank={idx + 1} best={idx === 0} compact onLoad={onLoad} /></div>
                </div>
              ))}
            </div>
            <div className="ask-car-nav">
              <span className="ask-car-dots">
                {results.map((r, idx) => (
                  <button key={r.label + r.rule_type} type="button" className={idx === active ? "is-on" : ""}
                    aria-label={`${idx + 1}위 조합 보기`} aria-current={idx === active} onClick={() => slideTo(idx)} />
                ))}
              </span>
              <span className="ask-car-cnt" aria-live="polite"><b className="num">{active + 1}</b> / {results.length} · 옆으로 밀어서 보기</span>
            </div>
          </div>
        ) : null}
        <p className="ask-disc ask-in" style={inStyle(results.length + 3)} role="note">{DISCLAIMER}</p>
        {state.answers.profile === "scalper" ? <p className="ask-note ask-in" style={inStyle(results.length + 4)} role="note">{SCALPER_NOTE}</p> : null}
        {extraBlock(results.length + 5)}
        {carousel ? null : followUps(results.length + 6)}
      </>
    );
  }

  const showProgress = asking;
  const answered = answeredSteps.length;
  const wide = state.phase === "candidates" || state.phase === "results" || state.phase === "loadingResults";
  const profileHint = PROFILES.find((p) => p.value === state.answers.profile)?.hint;

  return createPortal(
    <div className="ask-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
      <div role="dialog" aria-modal="true" aria-labelledby={titleId} tabIndex={-1} className={"ask-dlg" + (wide ? " is-wide" : "")}>
        <header className="ask-head">
          <img src={ASK_MASCOT} alt="" width="28" height="28" className="ask-head-face" aria-hidden="true" />
          <h2 id={titleId} className="ask-title">껄무새에게 물어볼까?</h2>
          {status && !status.error && status.daily_limit ? <span className="ask-quota num">오늘 {status.remaining_today}/{status.daily_limit}번 남음</span> : null}
          <button type="button" onClick={onClose} disabled={busy} className="ask-x" aria-label="닫기">×</button>
        </header>
        <div className="ask-body" ref={bodyRef}>
          {showProgress ? (
            <div className="ask-prog" aria-hidden="true">
              <span className="ask-prog-bar"><i style={{ width: `${(answered / STEPS.length) * 100}%` }} /></span>
              <span className="num">{Math.min(answered + 1, STEPS.length)} / {STEPS.length}</span>
            </div>
          ) : null}
          {status && status.consented && answeredSteps.length ? (
            <div className="ask-pills" aria-label="고른 답">
              {answeredSteps.map((s) => (
                <button key={s} type="button" className="ask-pill" title={BACK_TO_STEP} disabled={lock}
                  onClick={() => leaveThen(() => dispatch({ type: "back", step: s }))}>
                  {answerLabel(s, state.answers)}{s === "profile" && profileHint ? <small>{profileHint}</small> : null}
                </button>
              ))}
            </div>
          ) : null}
          <div key={stageKey} className={"ask-stage" + (leaving ? " is-leaving" : "")}>{stage}</div>
        </div>
        {carousel ? (
          <footer className="ask-foot">
            <button type="button" className="btn btn-l btn-primary ask-foot-load" onClick={() => onLoad(current.macro, current.label)}>{LOAD_BUTTON}</button>
            {followUps(0, "is-scroll")}
          </footer>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
