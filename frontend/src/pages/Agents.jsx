import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api.js";
import { useAuth, useAccountGuard } from "../lib/auth.js";
import { RULE_TYPES } from "../lib/macro.js";
import { computeSessionOverlay } from "../lib/indicators.js";
import { practiceModeLabel } from "../lib/exchanges.js";
import { useWhaleActivity } from "../features/agents/useWhaleActivity.js";
import { useAgentCommentary } from "../features/agents/useAgentCommentary.js";
import { readChatView, saveChatView } from "../lib/agentCommentary.js";
import { useSessionLogs } from "../features/agents/useSessionLogs.js";
import { positiveEventId } from "../features/agents/activityEvidence.js";
import AgentActivityStream from "../components/AgentActivityStream.jsx";
import AgentSessionLog from "../components/AgentSessionLog.jsx";
import "../components/AgentSessionPanel.css";
import CandleChart from "../components/CandleChart.jsx";
import PositionStrip from "../components/PositionStrip.jsx";
import { entryWaitFromServer, isWaitingMacro } from "../lib/entryWait.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { Loading } from "../components/Page.jsx";
import ConfirmDialog from "../components/ConfirmDialog.jsx";
import AppToast from "../components/AppToast.jsx";
import { describeDeleteConfirm, describeStopConfirm } from "../features/agents/runOutcome.js";
import { AgentHistoryList, AgentIdle, AgentViewTabs } from "../components/AgentHistory.jsx";
import { runPeriodLabel } from "../features/agents/history.js";
import { newlyStartedSession, requireSessionSnapshot } from "../features/agents/sessionSnapshot.js";

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
  const prefix = session.status === "stopped" ? "종료 · " : session.status === "error"
    ? "오류 · "
    : session.connected
      ? ""
      : "응답대기 · ";
  const net = session.testnet ? ` · ${practiceModeLabel(session.symbol, session.mode)}` : "";
  return `${prefix}${session.symbol} · ${ruleLabel(session.macro)}${net}`;
}

function MacroDock({ sessions, selected, busy, onChange, onStop, onDelete, lead = null }) {
  const running = selected.status === "running";
  const connected = running && selected.connected;
  const stopping = selected.stopping;
  // 응답대기(실행 중이지만 heartbeat 끊김)와 오류·종료 항목만 목록에서 지운다.
  // 서버도 같은 기준으로 막으므로 버튼 상태와 실제 결과가 어긋나지 않는다.
  const removable = !connected;

  return (
    <section className={`agent-macro-dock${lead ? " has-lead" : ""}`} aria-label="매크로 세션 선택과 제어">
      {lead ? <div className="agent-macro-dock-lead">{lead}</div> : null}
      <label className="agent-macro-dock-picker">
        <span className="sr-only">매크로 세션 선택</span>
        <span className="agent-macro-picker-row">
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
          {/* 새 실행 추가 — 빠른 실행 플로우(라이브러리·리더보드에서 골라 실행기 연결)로 간다. */}
          <Link to="/?run=1&step=1" className="btn btn-m btn-secondary agent-macro-new" aria-label="새 매크로 실행" title="빠른 실행에서 매크로를 골라 새로 시작해요">
            <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 3v10M3 8h10" /></svg>
          </Link>
        </span>
      </label>

      {/* 실행 상태·실행기 버전·매크로 출처는 오른쪽 열 위 포지션 블록(PositionStrip)이 맡는다. */}
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

// 종료 기록에서 연 세션의 실행 바 — 돌아가기 · 무엇을 보고 있는지 · 보관 · 목록에서 삭제.
function HistoryDock({ session, busy, pinBusy, onBack, onTogglePin, onDelete }) {
  return (
    <section className="agent-macro-dock has-lead" aria-label="종료 기록 보기">
      <div className="agent-macro-dock-lead">
        <button type="button" className="agent-back" onClick={onBack}>
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M10 3 5 8l5 5" /></svg>
          종료 기록
        </button>
      </div>
      <div className="agent-macro-dock-picker">
        <span className="agent-detail-label">
          {sessionOptionLabel(session).replace(/^(종료|오류) · /, "")}
          <small>{runPeriodLabel(session.started_at, session.stopped_at)}</small>
        </span>
      </div>
      <div className="agent-macro-dock-actions">
        <button type="button" className="btn btn-m btn-secondary" aria-pressed={!!session.pinned} disabled={pinBusy === session.session_id} onClick={() => onTogglePin(session)}>
          {session.pinned ? "보관 풀기" : "보관하기"}
        </button>
        <button type="button" className="btn btn-m btn-ghost" disabled={busy} onClick={onDelete}>목록에서 삭제</button>
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

export default function Agents() {
  const { accountVersion } = useAuth();
  return <AccountAgents key={accountVersion} />;
}

function AccountAgents() {
  const { token } = useAuth();
  const isCurrentAccount = useAccountGuard();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [sessions, setSessions] = useState(null);
  // 불러오기 오류(화면 위 띠로 띄우지 않는다 — 다음 갱신이 스스로 고친다)와 버튼 행동 오류(토스트)를 나눈다.
  const [error, setError] = useState("");
  const [actionError, setActionError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  // 확인 모달이 기다리는 동작: { type: "stop", mode } | { type: "delete" }
  const [pending, setPending] = useState(null);
  // 종료 요청을 서버 왕복 전에 화면에 먼저 반영한다: { id, mode }
  const [pendingStop, setPendingStop] = useState(null);
  const [mobilePane, setMobilePane] = useState(() => searchParams.get("panel") === "logs" ? "chat" : "chart");
  const [pinBusy, setPinBusy] = useState(0);
  const [logCategory, setLogCategory] = useState("all");
  const [logQuery, setLogQuery] = useState({});
  const [logsPaused, setLogsPaused] = useState(false);
  const [agentFilter, setAgentFilter] = useState("all");  // 채팅 탭(전체 · 거래 관련 · 뉴스 소식 · 손익 · 시세 …) — 실행마다 기억
  const [fileBusy, setFileBusy] = useState(0);
  const sessionSnapshotRevision = useRef(0);
  const lastStreamMessageAt = useRef(0);

  const loadSessions = useCallback(async (signal) => {
    if (!isCurrentAccount()) return;
    const revision = sessionSnapshotRevision.current;
    try {
      const data = await api.runnerSessions({ signal });
      if (!isCurrentAccount() || signal?.aborted || sessionSnapshotRevision.current !== revision) return;
      const snapshot = requireSessionSnapshot(data);
      sessionSnapshotRevision.current += 1;
      setSessions(snapshot);
      setError("");
    } catch (reason) {
      if (!isCurrentAccount() || signal?.aborted || sessionSnapshotRevision.current !== revision) return;
      setError(String(reason.message || reason));
    }
  }, [isCurrentAccount]);

  const pollSessions = useCallback(async (signal) => {
    if (!isCurrentAccount()) return;
    if (Date.now() - lastStreamMessageAt.current < 15000) return;
    await loadSessions(signal);
  }, [isCurrentAccount, loadSessions]);
  useAdaptivePolling(pollSessions, { intervalMs: 5000, maxIntervalMs: 15000,
    enabled: !!token, immediate: false, pollKey: token });

  useEffect(() => {
    if (!token) {
      navigate("/login?next=%2Fagents", { replace: true });
      return undefined;
    }
    let alive = true;
    const controller = new AbortController();
    const sessionRevision = sessionSnapshotRevision.current;
    api.runnerSessions({ signal: controller.signal })
      .then((data) => {
        if (!alive || !isCurrentAccount()) return;
        if (sessionSnapshotRevision.current === sessionRevision) {
          const snapshot = requireSessionSnapshot(data);
          sessionSnapshotRevision.current += 1;
          setSessions(snapshot);
        }
        setError("");
      })
      .catch((reason) => {
        if (alive && isCurrentAccount() && reason.name !== "AbortError") setError(String(reason.message || reason));
      });
    return () => { alive = false; controller.abort(); };
  }, [isCurrentAccount, navigate, token]);

  useEffect(() => {
    if (!token) return undefined;
    let stopped = false;
    let socket = null;
    let reconnectTimer = null;
    let reconnectAttempt = 0;

    const scheduleReconnect = () => {
      if (stopped || !isCurrentAccount() || reconnectTimer !== null) return;
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
      if (!isCurrentAccount()) return;
      try {
        const credential = await api.runnerSessionsStreamToken();
        if (stopped || !isCurrentAccount()) return;
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
          if (stopped || !isCurrentAccount() || socket !== nextSocket || message?.type !== "sessions.snapshot" || !message.data) return;
          try {
            requireSessionSnapshot(message.data);
          } catch (reason) {
            setError(reason.message);
            return;
          }
          lastStreamMessageAt.current = Date.now();
          setError("");
          sessionSnapshotRevision.current += 1;
          setSessions(message.data);
          reconnectAttempt = 0;
        };
        nextSocket.onerror = () => nextSocket.close();
        nextSocket.onclose = () => {
          if (socket === nextSocket) socket = null;
          if (!stopped && isCurrentAccount()) {
            lastStreamMessageAt.current = 0;
          }
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
  }, [isCurrentAccount, token]);

  // 선택의 소스는 '실행기에서 실제 구동 중인 세션'이다. 저장된 매크로 라이브러리가
  // 아니라 러너가 보고하는 active 세션을 그대로 쓴다. 단, 종료와 동시에 사라지는
  // 오류 상태는 같은 매크로가 다시 실행되기 전까지 최신 1건을 함께 보존한다.
  const activeSessions = useMemo(() => sessions?.active || [], [sessions]);
  const recentSessions = useMemo(() => sessions?.recent || [], [sessions]);
  // 드롭다운은 실행 중인 것만 고른다. 종료된 세션은 종료 기록 목록에서 연다(2026-10-02).
  const sessionOptions = useMemo(() => {
    if (!pendingStop) return activeSessions;
    return activeSessions.map((session) => (
      session.session_id === pendingStop.id && session.status === "running" && !session.stopping
        ? { ...session, stopping: true, stop_mode: pendingStop.mode }
        : session
    ));
  }, [activeSessions, pendingStop]);

  // 서버가 같은 상태를 보고하면 낙관적 표시를 거둔다.
  useEffect(() => {
    if (!pendingStop) return;
    const reported = [...activeSessions, ...(sessions?.recent || [])]
      .find((session) => session.session_id === pendingStop.id);
    if (!reported || reported.stopping || reported.status !== "running") setPendingStop(null);
  }, [activeSessions, pendingStop, sessions]);
  const selectedId = searchParams.get("session");
  const requestedLogEvent = searchParams.get("event");
  const logEventId = positiveEventId(requestedLogEvent);
  const anchoredLogs = searchParams.get("panel") === "logs" && requestedLogEvent !== null;
  const view = searchParams.get("view") === "history" ? "history" : "live";
  // 종료 기록에서 연 세션(또는 방금 종료해 기록으로 넘어간 세션)은 결과 화면으로 본다.
  const historySession = useMemo(
    () => recentSessions.find((session) => String(session.session_id) === selectedId) || null,
    [recentSessions, selectedId],
  );
  const mode = historySession ? "detail" : activeSessions.length ? (view === "history" ? "history" : "live") : "overview";
  const selected = useMemo(() => {
    if (historySession) return historySession;
    if (anchoredLogs) return sessionOptions.find((session) => String(session.session_id) === selectedId) || null;
    if (mode !== "live" || !sessionOptions.length) return null;
    return sessionOptions.find((session) => String(session.session_id) === selectedId) || sessionOptions[0];
  }, [anchoredLogs, historySession, mode, selectedId, sessionOptions]);

  useEffect(() => {
    if (mode !== "live" || !selected || String(selected.session_id) === selectedId) return;
    const next = new URLSearchParams(searchParams);
    next.set("session", String(selected.session_id));
    setSearchParams(next, { replace: true });
  }, [searchParams, selected, selectedId, setSearchParams]);

  // 끝난 세션의 결과 화면을 보는 중 실행기에서 새로 매크로를 시작하면 그 세션으로 넘어간다
  // (예: 모의로 잘못 시작 → 청산 후 종료 → 실전으로 다시 시작). 이미 실행 중이던 세션은 '새로' 가 아니다.
  const knownActiveRef = useRef(null);
  useEffect(() => {
    if (!sessions) return;
    if (knownActiveRef.current === null) {
      knownActiveRef.current = new Set(activeSessions.map((session) => session.session_id));
      return;
    }
    const started = !anchoredLogs && mode === "detail" ? newlyStartedSession(knownActiveRef.current, activeSessions, historySession) : null;
    for (const session of activeSessions) knownActiveRef.current.add(session.session_id);
    if (!started) return;
    const next = new URLSearchParams(searchParams);
    next.set("session", String(started.session_id));
    next.delete("view");
    next.delete("event");
    setSearchParams(next, { replace: true });
  }, [activeSessions, anchoredLogs, historySession, mode, searchParams, sessions, setSearchParams]);

  const whaleActivity = useWhaleActivity(selected);
  const logsOpen = searchParams.get("panel") === "logs";
  const logLinkInvalid = anchoredLogs && (!logEventId || !positiveEventId(selectedId) || !selected);
  const activeLogQuery = anchoredLogs && logEventId ? { contextId: logEventId } : logQuery;
  const activeLogCategory = anchoredLogs ? "all" : logCategory;
  // 껄무새 중계 — 서버가 만든 말풍선을 이어 받는다(뉴스 · 커뮤니티 글 · 체결 · 손실 구간도 이 안에 있다).
  const commentary = useAgentCommentary(selected);
  // 실행을 바꾸면 그 실행에서 마지막으로 고른 채팅 탭으로.
  const selectedSessionId = selected?.session_id;
  useEffect(() => { setAgentFilter(readChatView(selectedSessionId)); }, [selectedSessionId]);
  const sessionLogs = useSessionLogs(selected, { enabled: logsOpen && !logLinkInvalid, category: activeLogCategory, query: activeLogQuery, paused: logsPaused });

  useEffect(() => {
    setLogCategory("all");
    setLogQuery({});
    setLogsPaused(false);
  }, [selected?.session_id]);

  const macro = selected?.macro || null;
  const interval = macro?.candle_interval || "1d";
  const market = executionMarket(macro, selected);
  const exchange = macro?.exchange || "binance";
  const featureStates = useMemo(
    () => ({ whale_activity: whaleActivity }),
    [whaleActivity],
  );

  // 진입을 기다리는 매크로(돌파 · RSI · 볼린저 · 이평선 · 지정가 · 눌림 진입)가 포지션 없이 돌 때의 '무엇을 · 얼마나 · 언제'.
  // 차트는 사용자가 간격을 바꿀 수 있어 매크로 간격의 캔들을 따로 받는다.
  // 값은 서버가 보낸 entry_wait 만 쓴다(실행기 하트비트와 같은 값). 화면에서 공개 캔들로 다시 계산하던 예비 경로는
  // 이평선 필터 · 진입 필터를 몰라 안 살 봉에도 '마감되면 사요'라고 했다(2026-10-17 KRW-FORT) — 판정은 한 곳에서만.
  const waiting = selected?.status === "running" && !selected?.in_position && isWaitingMacro(macro);
  const serverWait = waiting ? selected?.entry_wait || null : null;
  const waitModel = useMemo(
    () => (serverWait ? entryWaitFromServer(serverWait, selected) : null),
    [serverWait, selected],
  );
  const waitLine = waitModel?.priceLine || null;

  // strategy_levels 는 서버가 체크포인트의 전략 상태로 계산한 **실제** 가격선이다.
  // 넘기지 않으면 화면이 '지금 가격에 진입한다고 가정한' 미리보기 선을 실행 화면에 그린다 —
  // E 눌림목에서 그 선이 가격을 따라 내려와 "곧 살 것처럼" 보였다.
  const chartOverlay = useCallback((candles) => {
    const overlay = computeSessionOverlay(
      macro,
      selected?.in_position ? selected.entry_price : null,
      selected?.position_side || macro?.position_side,
      candles,
      selected?.strategy_levels || null,
    );
    // 진입 대기 중이면 '사는 선' 을 굵게, 지금 가격과의 거리를 라벨에 붙여 얹는다.
    if (!overlay || !waitLine) return overlay;
    return { ...overlay, priceLines: [...(overlay.priceLines || []), { price: waitLine.price, color: "rgb(var(--chart-up))", label: waitLine.label }] };
  }, [macro, selected?.entry_price, selected?.in_position, selected?.position_side, waitLine,
      selected?.strategy_levels]);

  function changeSession(id) {
    const next = new URLSearchParams(searchParams);
    next.set("session", String(id));
    next.delete("event");
    setSearchParams(next);
    setMobilePane(logsOpen ? "chat" : "chart");
  }

  function showPanel(panel) {
    const next = new URLSearchParams(searchParams);
    if (panel === "logs") next.set("panel", "logs");
    else { next.delete("panel"); next.delete("event"); }
    setSearchParams(next, { replace: true });
  }

  function changeLogQuery(nextQuery) {
    const next = new URLSearchParams(searchParams);
    next.delete("event");
    setLogQuery(nextQuery);
    setSearchParams(next, { replace: true });
  }

  function changeLogCategory(category) {
    if (anchoredLogs) changeLogQuery({});
    setLogCategory(category);
  }

  function showView(nextView) {
    const next = new URLSearchParams(searchParams);
    next.delete("session");
    next.delete("event");
    if (nextView === "history") next.set("view", "history");
    else next.delete("view");
    setSearchParams(next);
  }

  function openHistory(session) {
    const next = new URLSearchParams(searchParams);
    next.delete("view");
    next.set("session", String(session.session_id));
    next.delete("event");
    setSearchParams(next);
    setMobilePane(logsOpen ? "chat" : "chart");
  }

  function backToHistory() {
    showView(activeSessions.length ? "history" : "live");
  }

  async function togglePin(session) {
    if (pinBusy || !isCurrentAccount()) return;
    setPinBusy(session.session_id);
    try {
      await api.runnerPinSession(session.session_id, !session.pinned);
      if (!isCurrentAccount()) return;
      setActionError("");
      await loadSessions();
    } catch (reason) {
      if (isCurrentAccount()) setActionError(String(reason.message || reason));
    } finally {
      if (isCurrentAccount()) setPinBusy(0);
    }
  }

  // 종료 기록의 매크로를 파일(.ggm.json)로 다시 받는다 — 직접 만들기의 '매크로 파일 내려받기' 와 같은 서명 파일.
  async function downloadSessionMacro(session) {
    if (fileBusy || !session?.macro || !isCurrentAccount()) return;
    setFileBusy(session.session_id);
    try {
      await api.downloadMacroFile(session.macro);
      if (isCurrentAccount()) setActionError("");
    } catch (reason) {
      if (isCurrentAccount()) setActionError(String(reason.message || reason));
    } finally {
      if (isCurrentAccount()) setFileBusy(0);
    }
  }

  function stopSession(mode) {
    if (!selected) return;
    setPending({ type: "stop", mode });
  }

  function deleteSession() {
    if (!selected) return;
    setPending({ type: "delete" });
  }

  async function confirmPending() {
    if (!pending || !selected || !isCurrentAccount()) return;
    const action = pending;
    const target = selected;
    if (action.type === "stop") {
      // 화면을 먼저 결과 화면으로 넘긴다. 실행기 확정은 그 안에서 기다린다.
      setPendingStop({ id: target.session_id, mode: action.mode });
      setPending(null);
    }
    setBusy(true);
    try {
      if (action.type === "stop") {
        await api.runnerRequestStop(target.session_id, action.mode);
      } else {
        await api.runnerDeleteSession(target.session_id);
        if (!isCurrentAccount()) return;
        const next = new URLSearchParams(searchParams);
        next.delete("session");
        if (target.status !== "running" && activeSessions.length) next.set("view", "history");
        setSearchParams(next, { replace: true });
      }
      if (!isCurrentAccount()) return;
      await loadSessions();
      if (!isCurrentAccount()) return;
      setPending(null);
    } catch (reason) {
      if (!isCurrentAccount()) return;
      setActionError(String(reason.message || reason));
      if (action.type === "stop") setPendingStop(null);
      setPending(null);
    } finally {
      if (isCurrentAccount()) setBusy(false);
    }
  }

  const confirmCopy = pending && selected
    ? (pending.type === "stop" ? describeStopConfirm(pending.mode, selected) : describeDeleteConfirm(selected))
    : null;

  if (!token) return <Loading label="로그인 화면으로 이동하는 중…" />;
  if (!sessions && !error) return <Loading label="실행 중인 매크로를 불러오는 중…" />;

  return (
    <div className="agent-page">
      {/* 화면 제목은 두지 않는 디자인(DESIGN.md §9) — 문서 구조와 화면 읽기 프로그램을 위한 제목만. */}
      <h1 className="sr-only">내 에이전트</h1>
      {!sessions && error ? (
        // 처음 불러오기가 실패해 보일 것이 없을 때만 — 화면 가운데 빈 화면으로. 이미 보이는 내용이 있으면 아무것도 띄우지 않는다.
        <div className="agent-load-failed" role="status">
          <strong>실행 상태를 불러오지 못했어요.</strong>
          <button type="button" className="btn btn-m btn-secondary" disabled={retrying} onClick={async () => {
            setRetrying(true);
            try { await loadSessions(); }
            finally { if (isCurrentAccount()) setRetrying(false); }
          }}>{retrying ? "불러오는 중…" : "다시 불러오기"}</button>
        </div>
      ) : null}
      {actionError ? <AppToast key={actionError} tone="error" label="실패" title={actionError} onClose={() => setActionError("")} /> : null}
      {sessions && logLinkInvalid ? <div className="agent-inline-error" role="alert">요청한 활동 로그의 세션 또는 사건 ID를 확인하지 못했어요. 다른 세션의 기록으로 대체하지 않습니다.
        <button type="button" className="btn btn-s btn-secondary" onClick={() => { showPanel("agent"); }}>연결 조회 해제</button></div> : null}
      {sessions && mode === "overview" ? (
        <div className="agent-overview">
          <AgentIdle last={recentSessions[0] || null} />
          <AgentHistoryList sessions={recentSessions} policy={sessions.history_policy} pinBusy={pinBusy} onOpen={openHistory} onTogglePin={togglePin} fileBusy={fileBusy} onDownload={downloadSessionMacro} />
        </div>
      ) : null}

      {sessions && mode === "history" ? (
        <div className="agent-overview">
          <AgentViewTabs view="history" activeCount={activeSessions.length} historyCount={recentSessions.length} onSelect={showView} />
          <AgentHistoryList sessions={recentSessions} policy={sessions.history_policy} pinBusy={pinBusy} onOpen={openHistory} onTogglePin={togglePin} fileBusy={fileBusy} onDownload={downloadSessionMacro} />
        </div>
      ) : null}

      {selected && !logLinkInvalid ? (
        <div className="agent-workspace">
          {mode === "detail" ? (
            <HistoryDock
              session={selected}
              busy={busy}
              pinBusy={pinBusy}
              onBack={backToHistory}
              onTogglePin={togglePin}
              onDelete={deleteSession}
            />
          ) : (
            <MacroDock
              sessions={sessionOptions}
              selected={selected}
              busy={busy}
              onChange={changeSession}
              onStop={stopSession}
              onDelete={deleteSession}
              lead={<AgentViewTabs view="live" activeCount={activeSessions.length} historyCount={recentSessions.length} onSelect={showView} />}
            />
          )}
          <WorkspaceTabs selected={mobilePane} onSelect={setMobilePane} />
          <div className={`agent-console is-${mobilePane}`}>
            <section className="agent-chart-pane" aria-label={`${selected.symbol} 실시간 차트`}>
              <div className="agent-chart-stage">
                <CandleChart
                  key={`${selected.session_id}-${exchange}-${market}`}
                  symbol={selected.symbol}
                  exchange={exchange}
                  market={market}
                  defaultInterval={interval}
                  expanded
                  minimal
                  overlay={chartOverlay}
                />
              </div>
            </section>

            {/* 오른쪽 열: 평가손익 블록(제일 중요한 지표)이 위, 에이전트 기록이 아래. 차트는 실행 바 바로 아래부터 전체 높이. */}
            <div className="agent-side">
              {/* 종료됐거나 종료 처리 중이면 오른쪽 열은 결과 화면이 맡는다 — 블록을 겹쳐 두지 않는다. */}
              {selected.status === "running" && !selected.stopping ? <PositionStrip session={selected} macro={macro} waitModel={waitModel} /> : null}
              <AgentActivityStream
                key={selected.session_id}
                symbol={selected.symbol}
                session={selected}
                featureStates={featureStates}
                commentary={commentary}
                selectedView={logsOpen ? "activity_logs" : agentFilter}
                onSelectView={(next) => {
                  if (next !== "activity_logs") {
                    setAgentFilter(next);
                    saveChatView(selected.session_id, next);
                  }
                  showPanel(next === "activity_logs" ? "logs" : "agent");
                }}
                activityLog={<div className="agent-session-panel">
                  {anchoredLogs && sessionLogs.state.status === "error" ? <div className="agent-receipt-error" role="alert">
                    <p>사건 #{logEventId} 원본 조회에 실패했어요. 삭제·보관 기한 또는 접근 권한을 확인해 주세요. 다른 사건으로 대체하지 않습니다.</p>
                  </div> : null}
                  <AgentSessionLog session={selected} state={sessionLogs.state} category={activeLogCategory}
                    onCategoryChange={changeLogCategory} query={activeLogQuery} onQueryChange={changeLogQuery}
                    paused={logsPaused} onPauseChange={setLogsPaused}
                    onRefresh={sessionLogs.refresh} onLoadMore={sessionLogs.loadMore} />
                </div>}
              />
            </div>
          </div>

        </div>
      ) : null}

      <ConfirmDialog
        open={!!confirmCopy}
        {...(confirmCopy || {})}
        busy={busy}
        onConfirm={confirmPending}
        onCancel={() => { if (!busy) setPending(null); }}
      />
    </div>
  );
}
