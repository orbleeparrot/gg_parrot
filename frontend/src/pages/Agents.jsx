import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth } from "../lib/auth.js";
import { RULE_TYPES } from "../lib/macro.js";
import { computeSessionOverlay } from "../lib/indicators.js";
import { usePositionNewsFeature } from "../features/agents/positionNews/index.js";
import AgentActivityStream from "../components/AgentActivityStream.jsx";
import CandleChart from "../components/CandleChart.jsx";
import { ErrorNote, Loading } from "../components/Page.jsx";

const SESSION_STREAM_PROTOCOL = "ggparrot.sessions.v1";
const SESSION_RECONNECT_MAX_MS = 30000;

function executionMarket(macro, session) {
  if (session?.market === "futures" || session?.market === "spot") return session.market;
  if (macro?.market === "futures" || macro?.market === "spot") return macro.market;
  if (macro?.rule_type === "K" || macro?.position_side === "short" || Number(macro?.leverage || 1) > 1) return "futures";
  return "spot";
}

function ruleLabel(macro) {
  return RULE_TYPES[macro?.rule_type]?.label || macro?.rule_type || "매크로";
}

// 셀렉트 옵션 라벨: 실행 중 세션과 마지막 오류를 종목·전략·환경 기준으로 표기한다.
function sessionOptionLabel(session) {
  const prefix = session.status === "error"
    ? "오류 · "
    : session.connected
      ? ""
      : "응답대기 · ";
  const net = session.testnet ? " · 테스트넷" : "";
  return `${prefix}${session.symbol} · ${ruleLabel(session.macro)}${net}`;
}

function statusText(session) {
  if (session.status === "error") return "실행 오류";
  if (session.status !== "running") return "실행 종료";
  if (session.stopping) return "종료 처리 중…";
  if (session.in_position) {
    const pct = Number(session.unrealized_pct ?? 0);
    return `보유 중 · 평가손익 ${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
  }
  return session.connected ? "실행 중 · 무포지션" : "응답 확인 중";
}

function MacroDock({ sessions, selected, busy, onChange, onStop, onDelete }) {
  const running = selected.status === "running";
  const connected = running && selected.connected;
  const stopping = selected.stopping;
  // 응답대기(실행 중이지만 heartbeat 끊김)와 오류·종료 항목만 목록에서 지운다.
  // 서버도 같은 기준으로 막으므로 버튼 상태와 실제 결과가 어긋나지 않는다.
  const removable = !connected;

  return (
    <section className="agent-macro-dock" aria-label="매크로 세션 선택과 제어">
      <label className="agent-macro-dock-picker">
        <span className="sr-only">매크로 세션 선택</span>
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
        <button type="button" disabled={busy || stopping || !running} onClick={() => onStop("stop_only")} className="btn btn-m btn-secondary">매크로만 종료</button>
        <button type="button" disabled={busy || stopping || !running} onClick={() => onStop("close_and_stop")} className="btn btn-m btn-danger">청산 후 종료</button>
        <button
          type="button"
          disabled={busy || !removable}
          onClick={onDelete}
          className="btn btn-m btn-ghost"
          title={removable ? "이 세션 기록을 목록에서 지워요" : "실행기가 응답 중이에요. 먼저 종료해 주세요."}
        >
          목록에서 삭제
        </button>
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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [mobilePane, setMobilePane] = useState("chart");
  const sessionSnapshotRevision = useRef(0);

  const loadSessions = useCallback(async () => {
    const revision = sessionSnapshotRevision.current;
    try {
      const data = await api.runnerSessions();
      if (sessionSnapshotRevision.current !== revision) return;
      sessionSnapshotRevision.current += 1;
      setSessions(data);
      setError("");
    } catch (reason) {
      if (sessionSnapshotRevision.current !== revision) return;
      setError(String(reason.message || reason));
    }
  }, []);

  useEffect(() => {
    if (!token) {
      navigate("/login?next=%2Fagents", { replace: true });
      return undefined;
    }
    let alive = true;
    const sessionRevision = sessionSnapshotRevision.current;
    api.runnerSessions()
      .then((data) => {
        if (!alive) return;
        if (sessionSnapshotRevision.current === sessionRevision) {
          sessionSnapshotRevision.current += 1;
          setSessions(data);
        }
        setError("");
      })
      .catch((reason) => {
        if (alive) setError(String(reason.message || reason));
      });
    return () => { alive = false; };
  }, [navigate, token]);

  useEffect(() => {
    if (!token) return undefined;
    let stopped = false;
    let socket = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;

    const scheduleReconnect = () => {
      if (stopped || reconnectTimer !== null) return;
      const delay = Math.min(
        SESSION_RECONNECT_MAX_MS,
        1000 * (2 ** Math.min(reconnectAttempt, 5)),
      );
      reconnectAttempt = Math.min(reconnectAttempt + 1, 6);
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, delay);
    };

    const connect = async () => {
      try {
        const credential = await api.runnerSessionsStreamToken();
        if (stopped) return;
        if (!credential?.token) throw new Error("실시간 연결 토큰이 없어요.");

        const nextSocket = new WebSocket(api.runnerSessionsStreamUrl(), [
          SESSION_STREAM_PROTOCOL,
          `ggp-auth.${credential.token}`,
        ]);
        socket = nextSocket;

        nextSocket.onmessage = (event) => {
          let message;
          try {
            message = JSON.parse(event.data);
          } catch (_) {
            return;
          }
          if (message?.type !== "sessions.snapshot" || !message.data) return;
          sessionSnapshotRevision.current += 1;
          setSessions(message.data);
          reconnectAttempt = 0;
        };
        nextSocket.onerror = () => nextSocket.close();
        nextSocket.onclose = () => {
          if (socket === nextSocket) socket = null;
          scheduleReconnect();
        };
      } catch (_) {
        scheduleReconnect();
      }
    };

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (socket) {
        socket.onclose = null;
        socket.close(1000, "page closed");
      }
    };
  }, [token]);

  // 선택의 소스는 '실행기에서 실제 구동 중인 세션'이다. 저장된 매크로 라이브러리가
  // 아니라 러너가 보고하는 active 세션을 그대로 쓴다. 단, 종료와 동시에 사라지는
  // 오류 상태는 같은 매크로가 다시 실행되기 전까지 최신 1건을 함께 보존한다.
  const activeSessions = useMemo(() => sessions?.active || [], [sessions]);
  const recentErrorSessions = useMemo(() => {
    const activeMacroIds = new Set(
      activeSessions
        .map((session) => session.user_macro_id)
        .filter((id) => id !== null && id !== undefined),
    );
    const seen = new Set();
    return (sessions?.recent || []).filter((session) => {
      if (session.status !== "error") return false;
      if (session.user_macro_id !== null && session.user_macro_id !== undefined) {
        if (activeMacroIds.has(session.user_macro_id) || seen.has(session.user_macro_id)) return false;
        seen.add(session.user_macro_id);
        return true;
      }
      const legacyKey = `${session.symbol}:${session.position_side}:${session.market}`;
      if (seen.has(legacyKey)) return false;
      seen.add(legacyKey);
      return true;
    });
  }, [activeSessions, sessions]);
  const sessionOptions = useMemo(
    () => [...activeSessions, ...recentErrorSessions],
    [activeSessions, recentErrorSessions],
  );
  const selectedId = searchParams.get("session");
  const selected = useMemo(() => {
    if (!sessionOptions.length) return null;
    return sessionOptions.find((session) => String(session.session_id) === selectedId) || sessionOptions[0];
  }, [selectedId, sessionOptions]);

  useEffect(() => {
    if (!selected || String(selected.session_id) === selectedId) return;
    const next = new URLSearchParams(searchParams);
    next.set("session", String(selected.session_id));
    setSearchParams(next, { replace: true });
  }, [searchParams, selected, selectedId, setSearchParams]);

  const activePositionNews = usePositionNewsFeature(selected?.session_id);

  useEffect(() => {
    setChartSnapshot(null);
  }, [selected?.session_id]);

  const macro = selected?.macro || null;
  const interval = macro?.candle_interval || "1d";
  const market = executionMarket(macro, selected);
  const activeChart = chartSnapshot?.symbol === selected?.symbol ? chartSnapshot : null;
  const featureStates = useMemo(
    () => ({ position_news: activePositionNews }),
    [activePositionNews],
  );

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

  async function deleteSession() {
    if (!selected) return;
    const label = selected.status === "error" ? "오류로 끝난" : "응답이 끊긴";
    if (!window.confirm(`${label} ${selected.symbol} 세션을 목록에서 지울까요? 기록만 사라지고 거래는 건드리지 않아요.`)) return;
    setBusy(true);
    try {
      await api.runnerDeleteSession(selected.session_id);
      const next = new URLSearchParams(searchParams);
      next.delete("session");
      setSearchParams(next, { replace: true });
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
      {sessions && sessionOptions.length === 0 ? <EmptyLibrary /> : null}

      {selected ? (
        <div className="agent-workspace">
          <MacroDock
            sessions={sessionOptions}
            selected={selected}
            busy={busy}
            onChange={changeSession}
            onStop={stopSession}
            onDelete={deleteSession}
          />
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
            </section>

            <AgentActivityStream
              key={selected.session_id}
              symbol={selected.symbol}
              macro={macro}
              session={selected}
              candles={activeChart?.candles || []}
              interval={activeChart?.interval || interval}
              observedAt={activeChart?.serverTime || 0}
              featureStates={featureStates}
            />
          </div>

          {activePositionNews.error ? <div className="agent-inline-error" role="status">포지션 맞춤 뉴스를 불러오지 못했어요. 다른 작업은 계속 갱신됩니다.</div> : null}
        </div>
      ) : null}
    </div>
  );
}
