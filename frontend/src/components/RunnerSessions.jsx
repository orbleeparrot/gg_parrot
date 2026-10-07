import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { captureAccountGuard, useAuth } from "../lib/auth.js";
import { SectionTitle, EmptyRow } from "./Page.jsx";
import CandleChart from "./CandleChart.jsx";
import { computeSessionOverlay } from "../lib/indicators.js";
import { RULE_TYPES } from "../lib/macro.js";
import { macroOriginBadge } from "../lib/macroOrigin.js";
import { formatQuoteAmount, practiceModeLabel } from "../lib/exchanges.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import ConfirmDialog from "./ConfirmDialog.jsx";
import { describeDeleteConfirm, describeStopConfirm } from "../features/agents/runOutcome.js";
import { RunnerKeyPanel } from "./RunnerKeyPanel.jsx";
import { Icon } from "./icons.jsx";

// 내 매크로 실행 현황 — 실행기(exe)가 올리는 세션을 실시간으로 보여주고,
// 원격 종료(매크로만 / 청산 후)를 요청한다.


const P = (n) => `${(n ?? 0).toLocaleString()}`;

function SessionCard({ s, onStop, onDelete, busy }) {
  const stopping = s.stopping;
  const stopped = s.status !== "running";
  // 응답이 끊긴 실행과 이미 끝난 기록만 목록에서 지운다(서버도 같은 기준).
  const removable = !(!stopped && s.connected);
  const up = (s.unrealized_pct ?? 0) >= 0;
  const realUp = (s.realized_pnl ?? 0) >= 0;
  // 실행 중인 세션엔 종목 실시간 차트를 붙이고, 빌더와 동일한 전략 보조지표(볼린저·
  // 이동평균·RSI 등)를 실행 중인 매크로 조건 그대로 그린다. 보유 중이면 내 평단도 표시.
  const [chartOpen, setChartOpen] = useState(true);
  const hasEntry = s.in_position && (s.entry_price ?? 0) > 0;
  const hasMacro = !!s.macro?.rule_type;
  const origin = macroOriginBadge(s);
  const ruleLabel = hasMacro ? RULE_TYPES[s.macro.rule_type]?.label : "";
  const chartInterval = s.macro?.candle_interval || "5m";
  const overlay = useCallback(
    (candles) => computeSessionOverlay(s.macro, hasEntry ? s.entry_price : null, s.position_side, candles),
    [s.macro, hasEntry, s.entry_price, s.position_side]
  );

  return (
    <div className="py-3 space-y-2">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="t-label font-bold text-slate-900 num">{s.symbol}</span>
          <span className="badge badge-flat">{s.market === "futures" ? `선물 ${s.leverage}배` : "현물"}</span>
          <span className="badge badge-flat">{s.position_side === "short" ? "숏" : "롱"}</span>
          <span className={"badge " + (s.testnet ? "badge-flat" : "badge-risk")}>
            {s.testnet ? practiceModeLabel(s.symbol, s.mode) : "메인넷(실거래)"}
          </span>
          {!stopped && (
            <span className={"badge " + (s.connected ? "badge-mine" : "badge-flat")}>
              {s.connected ? "🟢 연결됨" : "⚪ 연결 끊김"}
            </span>
          )}
          {origin && <span className={origin.className} title={origin.title}>{origin.label}</span>}
          {stopping && <span className="badge badge-risk">종료 처리 중…</span>}
          {stopped && <span className="badge badge-flat">{s.status === "error" ? "오류 종료" : "종료됨"}</span>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {!stopped && !stopping && (
            <>
              <button onClick={() => onStop(s.session_id, "stop_only")} disabled={busy}
                      className="btn btn-s btn-secondary">매크로만 종료</button>
              <button onClick={() => onStop(s.session_id, "close_and_stop")} disabled={busy}
                      className="btn btn-s btn-danger">청산 후 종료</button>
            </>
          )}
          {removable && (
            <button onClick={() => onDelete(s)} disabled={busy}
                    className="btn btn-s btn-ghost">목록에서 삭제</button>
          )}
        </div>
      </div>

      {s.human_summary && <div className="t-small text-slate-500 truncate">{s.human_summary}</div>}

      {!stopped && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="min-w-0">
            <div className="stat-label">현재가</div>
            <div className="t-label font-bold num text-slate-900">{P(s.last_price)}</div>
          </div>
          <div className="min-w-0">
            <div className="stat-label">포지션</div>
            <div className="t-label font-bold text-slate-900">
              {s.in_position ? `보유 ${P(s.position_qty)}` : "무포지션"}
            </div>
          </div>
          <div className="min-w-0">
            <div className="stat-label">평가손익</div>
            <div className={"t-label font-bold num " + (up ? "text-green-600" : "text-red-600")}>
              {s.in_position ? `${up ? "+" : ""}${(s.unrealized_pct ?? 0).toFixed(2)}%` : "-"}
            </div>
          </div>
          <div className="min-w-0">
            <div className="stat-label">누적 실현손익</div>
            <div className={"t-label font-bold num " + (realUp ? "text-green-600" : "text-red-600")}>
              {realUp ? "+" : ""}{formatQuoteAmount(s.realized_pnl ?? 0, s.symbol, { fixed: true })}
            </div>
          </div>
        </div>
      )}

      {/* 실시간 차트 + 내 평단 — 원격 구동 중인 종목을 바로 보여준다. */}
      {!stopped && s.symbol && (
        <div className="pt-1">
          <button
            type="button"
            onClick={() => setChartOpen((v) => !v)}
            className="w-full py-2 flex items-center justify-between gap-3 text-left border-t border-slate-200"
            aria-expanded={chartOpen}
          >
            <span className="t-small font-semibold text-slate-700">
              실시간 차트
              {hasMacro && <span className="ml-2 t-caption text-slate-500">{ruleLabel}</span>}
              {hasEntry ? (
                <span className="ml-2 t-caption text-indigo-800 num">내 평단 {P(s.entry_price)}</span>
              ) : (
                <span className="ml-2 t-caption text-slate-400">무포지션 — 평단 없음</span>
              )}
            </span>
            <span className="t-caption text-slate-400 inline-flex items-center gap-1" aria-hidden="true">{chartOpen ? "접기" : "펼치기"}<Icon name={chartOpen ? "chevronUp" : "chevronDown"} size={14} /></span>
          </button>
          {chartOpen && (
            <CandleChart
              symbol={s.symbol}
              market={s.market}
              defaultInterval={chartInterval}
              overlay={overlay}
            />
          )}
        </div>
      )}

      <div className="t-caption text-slate-500 num">
        시작 {s.started_kst}
        {!stopped && s.heartbeat_kst && <> · 최근 갱신 {s.heartbeat_kst}</>}
        {stopped && s.stopped_kst && <> · 종료 {s.stopped_kst}</>}
        {s.note && <span className="text-slate-600"> · {s.note}</span>}
      </div>
    </div>
  );
}

export default function RunnerSessions({
  showKey = true,
  showRunnerLink = true,
  embedded = false,
  title = "내 매크로 실행 현황",
}) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (signal) => {
    try {
      const d = await api.runnerSessions({ signal });
      setData(d);
      setErr("");
    } catch (e) {
      if (e?.name !== "AbortError") setErr(String(e.message || e));
      if (signal) throw e;
    }
  }, []);

  useAdaptivePolling(load, { intervalMs: 4_000, maxIntervalMs: 60_000 });

  // 확인 모달이 기다리는 동작: { type: "stop", mode, session } | { type: "delete", session }
  const [pending, setPending] = useState(null);

  function findSession(sessionId) {
    return [...(data?.active || []), ...(data?.recent || [])]
      .find((session) => session.session_id === sessionId) || { session_id: sessionId };
  }

  function onDelete(session) {
    setPending({ type: "delete", session });
  }

  function onStop(sessionId, mode) {
    setPending({ type: "stop", mode, session: findSession(sessionId) });
  }

  async function confirmPending() {
    if (!pending) return;
    setBusy(true);
    try {
      if (pending.type === "stop") await api.runnerRequestStop(pending.session.session_id, pending.mode);
      else await api.runnerDeleteSession(pending.session.session_id);
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
      setPending(null);
    }
  }

  const confirmCopy = pending
    ? (pending.type === "stop" ? describeStopConfirm(pending.mode, pending.session) : describeDeleteConfirm(pending.session))
    : null;

  const active = data?.active || [];
  const recent = data?.recent || [];
  const count = active.length;

  return (
    <section className={`${embedded ? "runner-session-board" : "pt-6 border-t border-slate-200"} space-y-4`}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <SectionTitle count={count} className="mb-0">{title}</SectionTitle>
        {showRunnerLink ? (
          <Link to="/?run=1&step=1" className="btn btn-s btn-secondary">매크로 만들기 가이드</Link>
        ) : null}
      </div>

      {showKey ? <RunnerKeyPanel /> : null}

      {err && <div className="t-small text-red-600">오류: {err}</div>}

      {active.length === 0 ? (
        <EmptyRow>
          지금 돌고 있는 매크로가 없어요. 빌더에서 매크로 파일(.ggm.json)을 내려받아
          <b className="text-slate-700"> 껄무새 매크로 실행기</b>에 넣고 시작하면 여기에 실시간으로 보여요.
        </EmptyRow>
      ) : (
        <div className="divide-y divide-slate-200">
          {active.map((s) => (
            <SessionCard key={s.session_id} s={s} onStop={onStop} onDelete={onDelete} busy={busy} />
          ))}
        </div>
      )}

      {recent.length > 0 && (
        <div className="pt-2">
          <div className="t-caption text-slate-500 mb-1">최근 종료</div>
          <div className="divide-y divide-slate-200">
            {recent.map((s) => (
              <SessionCard key={s.session_id} s={s} onStop={onStop} onDelete={onDelete} busy={busy} />
            ))}
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirmCopy}
        {...(confirmCopy || {})}
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => { if (!busy) setPending(null); }}
      />
    </section>
  );
}
