import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { RULE_TYPES } from "../lib/macro.js";
import { computeSessionOverlay } from "../lib/indicators.js";
import AgentActivityStream from "../components/AgentActivityStream.jsx";
import CandleChart from "../components/CandleChart.jsx";
import { ErrorNote, Loading } from "../components/Page.jsx";

const NEWS_POLL_MS = 5 * 60 * 1000;

function executionMarket(macro, session) {
  if (session?.market === "futures" || session?.market === "spot") return session.market;
  if (macro?.market === "futures" || macro?.market === "spot") return macro.market;
  if (macro?.rule_type === "K" || macro?.position_side === "short" || Number(macro?.leverage || 1) > 1) return "futures";
  return "spot";
}

function ruleLabel(macro) {
  return RULE_TYPES[macro?.rule_type]?.label || macro?.rule_type || "매크로";
}

// 셀렉트 옵션 라벨: 실행 중 세션을 종목·전략·(테스트넷) 기준으로 표기한다.
function sessionOptionLabel(session) {
  const prefix = session.connected ? "" : "응답대기 · ";
  const net = session.testnet ? " · 테스트넷" : "";
  return `${prefix}${session.symbol} · ${ruleLabel(session.macro)}${net}`;
}

function statusText(session) {
  if (session.stopping) return "종료 처리 중…";
  if (session.in_position) {
    const pct = Number(session.unrealized_pct ?? 0);
    return `보유 중 · 평가손익 ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
  }
  return session.connected ? "실행 중 · 무포지션" : "응답 확인 중";
}

function MacroDock({ sessions, selected, busy, onChange, onStop }) {
  const connected = selected.connected;
  const stopping = selected.stopping;

  return (
    <section className="agent-macro-dock" aria-label="실행 중 매크로 선택과 종료">
      <label className="agent-macro-dock-picker">
        <span>실행 중 매크로</span>
        <span className="agent-macro-select-wrap">
          <select value={String(selected.session_id)} onChange={(event) => onChange(event.target.value)}>
            {sessions.map((session) => (
              <option key={session.session_id} value={String(session.session_id)}>
                {sessionOptionLabel(session)}
              </option>
            ))}
          </select>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 6 4 4 4-4" /></svg>
        </span>
      </label>

      <div className="agent-macro-dock-status" aria-label={`실행 상태: ${statusText(selected)}`}>
        <i className={`agent-live-dot ${connected ? "is-running" : "is-checking"}`} aria-hidden="true" />
        <span>{statusText(selected)}</span>
      </div>

      <div className="agent-macro-dock-actions">
        <button type="button" disabled={busy || stopping} onClick={() => onStop("stop_only")} className="btn btn-m btn-secondary">매크로만 종료</button>
        <button type="button" disabled={busy || stopping} onClick={() => onStop("close_and_stop")} className="btn btn-m btn-danger">청산 후 종료</button>
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
      <p className="t-caption text-slate-500">실행 중 매크로 없음</p>
      <h2 className="t-h3 text-slate-900">지금 실행기에서 구동 중인 매크로가 없어요.</h2>
      <p className="t-small text-slate-700 measure">
        껄무새 매크로 실행기에 매크로 파일(.ggm.json)을 넣고 시작하면, 이 화면에서 실시간 차트와 함께 상태를 확인하고 바로 종료할 수 있어요.
      </p>
      <div className="agent-empty-actions">
        <Link to="/?run=1&step=1" className="btn btn-l btn-primary">실행 가이드 보기</Link>
        <Link to="/builder" className="btn btn-l btn-secondary">직접 만들기</Link>
      </div>
    </section>
  );
}

export default function Agents() {
  const { token } = useAuth();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState(null);
  const [chartSnapshot, setChartSnapshot] = useState(null);
  const [news, setNews] = useState({ status: "idle", symbol: "", data: null, error: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
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
    api.runnerSessions()
      .then((data) => {
        if (!alive) return;
        setSessions(data);
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

  // 선택의 소스는 '실행기에서 실제 구동 중인 세션'이다. 저장된 매크로 라이브러리가
  // 아니라 러너가 보고하는 active 세션을 그대로 쓰므로 실행 정보와 항상 일치한다.
  const activeSessions = useMemo(() => sessions?.active || [], [sessions]);
  const selectedId = searchParams.get("session");
  const selected = useMemo(() => {
    if (!activeSessions.length) return null;
    return activeSessions.find((session) => String(session.session_id) === selectedId) || activeSessions[0];
  }, [activeSessions, selectedId]);

  useEffect(() => {
    if (!selected || String(selected.session_id) === selectedId) return;
    const next = new URLSearchParams(searchParams);
    next.set("session", String(selected.session_id));
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
  }, [loadNews, selected?.session_id, selected?.symbol]);

  const macro = selected?.macro || null;
  const interval = macro?.candle_interval || "1d";
  const market = executionMarket(macro, selected);
  const activeChart = chartSnapshot?.symbol === selected?.symbol ? chartSnapshot : null;
  const activeNews = news.symbol === selected?.symbol ? news : { status: "idle", data: null, error: "" };

  const chartOverlay = useCallback((candles) => computeSessionOverlay(
    macro,
    selected?.in_position ? selected.entry_price : null,
    selected?.position_side || macro?.position_side,
    candles,
  ), [macro, selected?.entry_price, selected?.in_position, selected?.position_side]);

  function changeSession(id) {
    const next = new URLSearchParams(searchParams);
    next.set("session", String(id));
    setSearchParams(next);
    setMobilePane("chart");
  }

  async function stopSession(mode) {
    if (!selected) return;
    const label = mode === "close_and_stop" ? "청산 후 종료" : "매크로만 종료";
    if (!window.confirm(`${label} 할까요? 실행기가 다음 확인에서 반영해요.`)) return;
    setBusy(true);
    try {
      await api.runnerRequestStop(selected.session_id, mode);
      await loadSessions();
    } catch (reason) {
      setError(String(reason.message || reason));
    } finally {
      setBusy(false);
    }
  }

  if (!token) return null;
  if (!sessions && !error) return <Loading label="실행 중인 매크로를 불러오는 중…" />;

  return (
    <div className="agent-page">
      {error ? <ErrorNote>실행 상태 오류: {error}</ErrorNote> : null}
      {sessions && activeSessions.length === 0 ? <EmptyLibrary /> : null}

      {selected ? (
        <div className="agent-workspace">
          <WorkspaceTabs selected={mobilePane} onSelect={setMobilePane} />
          <div className={`agent-console is-${mobilePane}`}>
            <section className="agent-chart-pane" aria-label={`${selected.symbol} 실시간 차트`}>
              <div className="agent-chart-stage">
                <CandleChart
                  key={`${selected.session_id}-${market}`}
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
                sessions={activeSessions}
                selected={selected}
                busy={busy}
                onChange={changeSession}
                onStop={stopSession}
              />
            </section>

            <AgentActivityStream
              key={selected.session_id}
              symbol={selected.symbol}
              macro={macro}
              session={selected}
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
