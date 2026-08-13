import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { RULE_TYPES } from "../lib/macro.js";
import { computeSessionOverlay } from "../lib/indicators.js";
import AgentActivityStream from "../components/AgentActivityStream.jsx";
import CandleChart from "../components/CandleChart.jsx";
import { ErrorNote, Loading } from "../components/Page.jsx";
import { RunnerKeyPanel } from "../components/RunnerSessions.jsx";

const NEWS_POLL_MS = 5 * 60 * 1000;
function orderedJson(value) {
  if (Array.isArray(value)) return value.map(orderedJson);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((result, key) => {
      result[key] = orderedJson(value[key]);
      return result;
    }, {});
  }
  return value;
}

function macroSignature(macro) {
  if (!macro || typeof macro !== "object") return "";
  try {
    return JSON.stringify(orderedJson(macro));
  } catch (_) {
    return "";
  }
}

function performanceLabel(performance) {
  const value = performance?.return_pct;
  if (!Number.isFinite(value)) return "집계 전";
  const prefix = performance.kind === "backtest" ? "백테스트" : "모의";
  return `${prefix} ${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function executionMarket(macro, session) {
  if (session?.market === "futures" || session?.market === "spot") return session.market;
  if (macro?.market === "futures" || macro?.market === "spot") return macro.market;
  if (macro?.rule_type === "K" || macro?.position_side === "short" || Number(macro?.leverage || 1) > 1) return "futures";
  return "spot";
}

function MacroDock({
  items,
  selected,
  currentSession,
  runningIds,
  busy,
  connectionOpen,
  onConnectionToggle,
  onChange,
  onStop,
}) {
  const running = currentSession?.status === "running";
  const connected = running && currentSession.connected;
  const macro = selected.macro || {};
  const interval = macro.candle_interval || "1d";
  const market = executionMarket(macro, currentSession);

  return (
    <section className="agent-macro-dock" aria-label="내 매크로 선택과 실행">
      <label className="agent-macro-dock-picker">
        <span>내 매크로</span>
        <span className="agent-macro-select-wrap">
          <select value={selected.id} onChange={(event) => onChange(event.target.value)}>
            {items.map((item) => (
              <option key={item.id} value={item.id}>
                {runningIds.has(String(item.id)) ? "실행 중 · " : ""}{item.name} · {item.symbol}
              </option>
            ))}
          </select>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 6 4 4 4-4" /></svg>
        </span>
      </label>

      <div className="agent-macro-dock-summary">
        <strong><b className="num">{selected.symbol}</b> {selected.name}</strong>
        <span>
          {RULE_TYPES[selected.rule_type]?.label || selected.rule_type} · <b className="num">{interval}</b> · {market === "futures" ? `선물 ${macro.leverage || 1}배` : "현물"}
        </span>
      </div>

      <div className="agent-macro-dock-status" aria-label={`실행 상태: ${connected ? "실행 중" : running ? "응답 확인 중" : "실행 대기"}`}>
        <i className={`agent-live-dot ${connected ? "is-running" : running ? "is-checking" : ""}`} aria-hidden="true" />
        <span>{connected ? "실행 중" : running ? "응답 확인 중" : performanceLabel(selected.performance)}</span>
      </div>

      <div className="agent-macro-dock-actions">
        <details
          className="agent-connection-menu"
          open={connectionOpen}
          onToggle={(event) => onConnectionToggle(event.currentTarget.open)}
        >
          <summary>연결 설정</summary>
          <div className="agent-connection-popover">
            <RunnerKeyPanel enabled={connectionOpen} />
          </div>
        </details>
        {running ? (
          <details className="agent-run-menu">
            <summary className="btn btn-m btn-secondary">실행 관리</summary>
            <div className="agent-run-popover">
              <strong>실행 중인 매크로</strong>
              <button type="button" disabled={busy || currentSession.stopping} onClick={() => onStop("stop_only")} className="btn btn-m btn-secondary">매크로만 종료</button>
              <button type="button" disabled={busy || currentSession.stopping} onClick={() => onStop("close_and_stop")} className="btn btn-m btn-danger">청산 후 종료</button>
            </div>
          </details>
        ) : (
          <Link to="/?run=1&step=1" state={{ selectedMacroId: selected.id }} className="btn btn-m btn-primary">매크로 실행</Link>
        )}
      </div>
    </section>
  );
}

function WorkspaceTabs({ selected, onSelect }) {
  return (
    <div className="agent-workspace-tabs" aria-label="에이전트 작업 화면">
      <button type="button" className={selected === "chart" ? "is-active" : ""} aria-pressed={selected === "chart"} onClick={() => onSelect("chart")}>차트</button>
      <button type="button" className={selected === "chat" ? "is-active" : ""} aria-pressed={selected === "chat"} onClick={() => onSelect("chat")}>에이전트</button>
    </div>
  );
}

function EmptyLibrary() {
  return (
    <section className="agent-empty">
      <p className="t-caption text-slate-500">연결할 매크로 없음</p>
      <h2 className="t-h3 text-slate-900">먼저 내 계정에 매크로를 추가해 주세요.</h2>
      <p className="t-small text-slate-700 measure">
        리더보드의 매크로를 가져오거나 직접 만든 매크로를 저장하면 이 화면에서 하나씩 선택해 관리할 수 있어요.
      </p>
      <div className="agent-empty-actions">
        <Link to="/leaderboard" className="btn btn-l btn-primary">리더보드 둘러보기</Link>
        <Link to="/builder" className="btn btn-l btn-secondary">직접 만들기</Link>
      </div>
    </section>
  );
}

export default function Agents() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [macros, setMacros] = useState(null);
  const [sessions, setSessions] = useState(null);
  const [chartSnapshot, setChartSnapshot] = useState(null);
  const [news, setNews] = useState({ status: "idle", symbol: "", data: null, error: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [connectionSettingsOpen, setConnectionSettingsOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState("chart");
  const sessionTimer = useRef(null);
  const newsTimer = useRef(null);
  const newsRequest = useRef(0);

  const loadSessions = useCallback(async () => {
    try {
      const data = await api.runnerSessions();
      setSessions(data);
      setError("");
    } catch (reason) {
      setError(String(reason.message || reason));
    }
  }, []);

  const loadNews = useCallback(async (symbol) => {
    if (!symbol) return;
    const requestId = newsRequest.current + 1;
    newsRequest.current = requestId;
    setNews((current) => ({
      status: "loading",
      symbol,
      data: current.symbol === symbol ? current.data : null,
      error: "",
    }));
    try {
      const data = await api.newsCoin(symbol);
      if (newsRequest.current === requestId) setNews({ status: "ready", symbol, data, error: "" });
    } catch (reason) {
      if (newsRequest.current === requestId) {
        setNews((current) => ({
          status: "error",
          symbol,
          data: current.symbol === symbol ? current.data : null,
          error: String(reason.message || reason),
        }));
      }
    }
  }, []);

  useEffect(() => {
    if (!token) {
      navigate("/login?next=%2Fagents", { replace: true });
      return undefined;
    }
    let alive = true;
    Promise.all([api.myMacros(), api.runnerSessions()])
      .then(([macroData, sessionData]) => {
        if (!alive) return;
        setMacros(Array.isArray(macroData?.items) ? macroData.items : []);
        setSessions(sessionData);
        setError("");
      })
      .catch((reason) => {
        if (alive) setError(String(reason.message || reason));
      });
    return () => { alive = false; };
  }, [navigate, token]);

  useEffect(() => {
    if (!token) return undefined;
    const poll = () => {
      if (!document.hidden) loadSessions();
      sessionTimer.current = window.setTimeout(poll, 4000);
    };
    sessionTimer.current = window.setTimeout(poll, 4000);
    return () => window.clearTimeout(sessionTimer.current);
  }, [loadSessions, token]);

  const sessionIndex = useMemo(() => {
    const index = new Map();
    const all = [...(sessions?.active || []), ...(sessions?.recent || [])];
    all.forEach((session) => {
      const signature = macroSignature(session.macro);
      if (!signature) return;
      if (!index.has(signature)) index.set(signature, []);
      index.get(signature).push(session);
    });
    return index;
  }, [sessions]);

  const selectedId = searchParams.get("macro");
  const activeSignatures = useMemo(
    () => new Set((sessions?.active || []).map((session) => macroSignature(session.macro)).filter(Boolean)),
    [sessions],
  );
  const selected = useMemo(() => {
    if (!macros?.length) return null;
    const requested = macros.find((item) => String(item.id) === selectedId);
    if (requested) return requested;
    const activeSession = (sessions?.active || []).find((session) => session.status === "running");
    const activeSignature = macroSignature(activeSession?.macro);
    return macros.find((item) => activeSignature && macroSignature(item.macro) === activeSignature) || macros[0];
  }, [macros, selectedId, sessions]);

  useEffect(() => {
    if (!selected || String(selected.id) === selectedId) return;
    const next = new URLSearchParams(searchParams);
    next.set("macro", String(selected.id));
    setSearchParams(next, { replace: true });
  }, [searchParams, selected, selectedId, setSearchParams]);

  useEffect(() => {
    if (!selected?.symbol) return undefined;
    setChartSnapshot(null);
    setNews({ status: "idle", symbol: selected.symbol, data: null, error: "" });
    loadNews(selected.symbol);
    const poll = () => {
      if (!document.hidden) loadNews(selected.symbol);
      newsTimer.current = window.setTimeout(poll, NEWS_POLL_MS);
    };
    newsTimer.current = window.setTimeout(poll, NEWS_POLL_MS);
    return () => {
      newsRequest.current += 1;
      window.clearTimeout(newsTimer.current);
    };
  }, [loadNews, selected?.id, selected?.symbol]);

  const runningIds = useMemo(() => {
    return new Set((macros || []).filter((item) => activeSignatures.has(macroSignature(item.macro))).map((item) => String(item.id)));
  }, [activeSignatures, macros]);

  const selectedSessions = selected ? (sessionIndex.get(macroSignature(selected.macro)) || []) : [];
  const currentSession = selectedSessions.find((session) => session.status === "running") || selectedSessions[0] || null;
  const macro = selected?.macro || null;
  const interval = macro?.candle_interval || "1d";
  const market = executionMarket(macro, currentSession);
  const activeChart = chartSnapshot?.symbol === selected?.symbol ? chartSnapshot : null;
  const activeNews = news.symbol === selected?.symbol ? news : { status: "idle", data: null, error: "" };

  const chartOverlay = useCallback((candles) => computeSessionOverlay(
    macro,
    currentSession?.in_position ? currentSession.entry_price : null,
    currentSession?.position_side || macro?.position_side,
    candles,
  ), [currentSession?.entry_price, currentSession?.in_position, currentSession?.position_side, macro]);

  function changeMacro(id) {
    const next = new URLSearchParams(searchParams);
    next.set("macro", String(id));
    setSearchParams(next);
    setMobilePane("chart");
  }

  async function stopSession(mode) {
    if (!currentSession) return;
    const label = mode === "close_and_stop" ? "청산 후 종료" : "매크로만 종료";
    if (!window.confirm(`${label} 할까요? 실행기가 다음 확인에서 반영해요.`)) return;
    setBusy(true);
    try {
      await api.runnerRequestStop(currentSession.session_id, mode);
      await loadSessions();
    } catch (reason) {
      setError(String(reason.message || reason));
    } finally {
      setBusy(false);
    }
  }

  if (!token) return null;
  if (!macros && !error) return <Loading label="내 에이전트 화면을 준비하는 중…" />;

  return (
    <div className="agent-page">
      {error ? <ErrorNote>실행 상태 오류: {error}</ErrorNote> : null}
      {macros?.length === 0 ? <EmptyLibrary /> : null}

      {selected ? (
        <div className="agent-workspace">
          <WorkspaceTabs selected={mobilePane} onSelect={setMobilePane} />
          <div className={`agent-console is-${mobilePane}`}>
            <section className="agent-chart-pane" aria-label={`${selected.symbol} 실시간 차트`}>
              <div className="agent-chart-stage">
                <CandleChart
                  key={`${selected.id}-${market}`}
                  symbol={selected.symbol}
                  market={market}
                  defaultInterval={interval}
                  expanded
                  minimal
                  overlay={chartOverlay}
                  onData={setChartSnapshot}
                />
              </div>

              <MacroDock
                items={macros}
                selected={selected}
                currentSession={currentSession}
                runningIds={runningIds}
                busy={busy}
                connectionOpen={connectionSettingsOpen}
                onConnectionToggle={setConnectionSettingsOpen}
                onChange={changeMacro}
                onStop={stopSession}
              />
            </section>

            <AgentActivityStream
              key={selected.id}
              symbol={selected.symbol}
              macro={macro}
              session={currentSession}
              candles={activeChart?.candles || []}
              interval={activeChart?.interval || interval}
              observedAt={activeChart?.serverTime || 0}
              news={activeNews.data}
              newsState={activeNews.status}
              onRefreshNews={() => loadNews(selected.symbol)}
            />
          </div>

          {activeNews.error ? <div className="agent-inline-error" role="status">종목 뉴스를 불러오지 못했어요. 다른 작업은 계속 갱신됩니다.</div> : null}
        </div>
      ) : null}
    </div>
  );
}
