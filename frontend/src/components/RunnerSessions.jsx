import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { SectionTitle, EmptyRow } from "./Page.jsx";
import CandleChart from "./CandleChart.jsx";
import { computeSessionOverlay } from "../lib/indicators.js";
import { RULE_TYPES } from "../lib/macro.js";

// 내 매크로 실행 현황 — 실행기(exe)가 올리는 세션을 실시간으로 보여주고,
// 원격 종료(매크로만 / 청산 후)를 요청한다.

export function RunnerKeyPanel({ enabled = true }) {
  const [data, setData] = useState(null);
  const [revealed, setRevealed] = useState(false);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    api.runnerKey()
      .then((d) => alive && setData(d))
      .catch((e) => alive && setErr(String(e.message || e)));
    return () => { alive = false; };
  }, [enabled]);

  if (!enabled) return null;

  async function regen() {
    if (!window.confirm("새 키를 발급하면 기존 키는 즉시 무효화돼요. 실행기에 새 키를 다시 입력해야 해요. 계속할까요?"))
      return;
    try {
      setData(await api.runnerKeyRegenerate());
      setRevealed(true);
    } catch (e) {
      setErr(String(e.message || e));
    }
  }

  async function copy() {
    if (!data?.key) return;
    try {
      await navigator.clipboard.writeText(data.key);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (_) {}
  }

  if (err) return <div className="t-small text-red-600">키 조회 오류: {err}</div>;
  if (!data) return <div className="t-small text-slate-500">키 불러오는 중…</div>;

  const masked = data.key.slice(0, 8) + "•".repeat(Math.max(0, data.key.length - 12)) + data.key.slice(-4);
  return (
    <div className="notice space-y-2">
      <div className="t-small text-slate-700">
        아래 <b className="text-slate-900">껄무새 회원 키</b>를 매크로 실행기의 ④번 칸에 입력하세요. 계정당 1개예요.
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <code className="num text-slate-900 bg-slate-100 px-2 py-1 rounded break-all">
          {revealed ? data.key : masked}
        </code>
        <button onClick={() => setRevealed((v) => !v)} className="btn btn-s btn-secondary">
          {revealed ? "숨기기" : "보기"}
        </button>
        <button onClick={copy} className="btn btn-s btn-secondary">{copied ? "복사됨!" : "복사"}</button>
        <button onClick={regen} className="btn btn-s btn-secondary">키 재발급</button>
      </div>
      <div className="t-caption text-slate-500">
        이 키는 서버 상태 확인·원격 종료에만 쓰여요. 거래소 API 키는 실행기에서 로컬로만 쓰고 서버로 보내지 않아요.
      </div>
    </div>
  );
}

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
            {s.testnet ? "테스트넷" : "메인넷(실거래)"}
          </span>
          {!stopped && (
            <span className={"badge " + (s.connected ? "badge-mine" : "badge-flat")}>
              {s.connected ? "🟢 연결됨" : "⚪ 연결 끊김"}
            </span>
          )}
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
              {realUp ? "+" : ""}{(s.realized_pnl ?? 0).toFixed(2)} USDT
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
            <span className="t-caption text-slate-400" aria-hidden="true">{chartOpen ? "접기 ↑" : "펼치기 ↓"}</span>
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
  const timer = useRef(null);

  const load = useCallback(async () => {
    try {
      const d = await api.runnerSessions();
      setData(d);
      setErr("");
    } catch (e) {
      setErr(String(e.message || e));
    }
  }, []);

  useEffect(() => {
    load();
    const poll = () => { load(); timer.current = setTimeout(poll, 4000); };
    timer.current = setTimeout(poll, 4000);
    return () => clearTimeout(timer.current);
  }, [load]);

  async function onDelete(session) {
    const label = session.status === "error" ? "오류로 끝난" : "응답이 끊긴";
    if (!window.confirm(`${label} ${session.symbol} 세션을 목록에서 지울까요? 기록만 사라지고 거래는 건드리지 않아요.`)) return;
    setBusy(true);
    try {
      await api.runnerDeleteSession(session.session_id);
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

  async function onStop(sessionId, mode) {
    const label = mode === "close_and_stop" ? "청산 후 종료" : "매크로만 종료";
    if (!window.confirm(`${label} 할까요? 실행기가 다음 확인에서 반영해요(최대 몇 초 지연).`)) return;
    setBusy(true);
    try {
      await api.runnerRequestStop(sessionId, mode);
      await load();
    } catch (e) {
      setErr(String(e.message || e));
    } finally {
      setBusy(false);
    }
  }

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
    </section>
  );
}
