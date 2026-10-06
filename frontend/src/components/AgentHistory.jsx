// 내 에이전트 — 실행 중이 없을 때의 첫 화면(AgentIdle), 종료 기록 괘선 목록(AgentHistoryList),
// 실행 중이 있을 때 위에 두는 '실행 중 n · 종료 기록 n' 전환(AgentViewTabs). 2026-10-02 개편.
import { Link } from "react-router-dom";
import CoinIcon from "./CoinIcon.jsx";
import { RULE_TYPES } from "../lib/macro.js";
import { baseOf, quoteOf } from "../lib/format.js";
import { formatSignedMoney, toneOf } from "../features/agents/runOutcome.js";
import { endLabel, environmentLabel, runDurationLabel, runPeriodLabel } from "../features/agents/history.js";
import "./AgentHistory.css";


function strategyLine(session) {
  const summary = String(session.human_summary || "").trim();
  if (summary) {
    // 요약 첫 조각은 종목(예: 'PUMP · 롱 · …') — 종목 칸과 겹치므로 뺀다.
    const parts = summary.split(" · ");
    return parts[0] === baseOf(session.symbol) ? parts.slice(1).join(" · ") : summary;
  }
  return RULE_TYPES[session.macro?.rule_type]?.label || "매크로";
}

export function AgentViewTabs({ view, activeCount, historyCount, onSelect }) {
  return (
    <div className="agent-view-tabs" role="tablist" aria-label="내 에이전트 보기">
      <button type="button" role="tab" aria-selected={view === "live"} className={view === "live" ? "is-active" : ""} onClick={() => onSelect("live")}>
        실행 중<span className="num">{activeCount}</span>
      </button>
      <button type="button" role="tab" aria-selected={view === "history"} className={view === "history" ? "is-active" : ""} onClick={() => onSelect("history")}>
        종료 기록<span className="num">{historyCount}</span>
      </button>
    </div>
  );
}

export function AgentIdle({ last }) {
  const lastEnd = last ? endLabel(last) : null;
  return (
    <section className="agent-idle" aria-labelledby="agent-idle-title">
      <img className="agent-idle-art" src="/brand/agent/ggparrot-agent-focused-v1.svg" alt="" width="88" height="88" draggable="false" decoding="async" />
      <div className="agent-idle-body">
        <h2 id="agent-idle-title">지금 돌아가는 에이전트가 없어요</h2>
        <p>실행기에서 매크로를 시작하면 여기서 실시간 차트·평가손익·에이전트 기록을 볼 수 있어요.</p>
        {last ? (
          <p className="agent-idle-last">
            마지막 실행 · <b>{baseOf(last.symbol)}</b> {strategyLine(last).split(" · ").slice(0, 2).join(" · ")}
            {" · "}{runPeriodLabel(last.stopped_at || last.started_at)} 종료 · {lastEnd.text}
          </p>
        ) : null}
        <div className="agent-idle-actions">
          <Link to="/?run=1&step=1" className="btn btn-l btn-primary">매크로 실행하기</Link>
          <Link to="/builder" className="btn btn-l btn-secondary">직접 만들기</Link>
        </div>
      </div>
    </section>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M4 2h8v12l-4-3-4 3z" /></svg>
  );
}

export function AgentHistoryList({ sessions, policy, pinBusy, onOpen, onTogglePin }) {
  const keep = policy?.keep ?? 30;
  const days = policy?.days ?? 30;
  const pinLimit = policy?.pin_limit ?? 10;
  const pinnedCount = sessions.filter((s) => s.pinned).length;
  return (
    <section className="agent-history" aria-labelledby="agent-history-title">
      <div className="agent-history-head">
        <h3 id="agent-history-title">종료 기록<span className="num">{sessions.length}</span></h3>
        <p>최근 {keep}건 · {days}일 보관 · <b className="num">보관 {pinnedCount}/{pinLimit}</b> 은 지우지 않아요</p>
      </div>
      {sessions.length === 0 ? (
        <p className="agent-history-empty">아직 종료한 에이전트가 없어요.</p>
      ) : (
        <div className="agent-history-table" role="table" aria-label="종료한 에이전트">
          <div className="agent-history-row is-head" role="row">
            <span role="columnheader" className="sr-only">로고</span>
            <span role="columnheader">종목</span>
            <span role="columnheader">전략</span>
            <span role="columnheader">실행 시간</span>
            <span role="columnheader">종료</span>
            <span role="columnheader" className="is-right">실현손익</span>
            <span role="columnheader" className="is-center">보관</span>
          </div>
          {sessions.map((session) => {
            const end = endLabel(session);
            const tone = toneOf(session.realized_pnl);
            const duration = runDurationLabel(session.started_at, session.stopped_at);
            return (
              <div
                key={session.session_id}
                className={`agent-history-row${session.pinned ? " is-pinned" : ""}`}
                role="row"
                tabIndex={0}
                onClick={() => onOpen(session)}
                onKeyDown={(event) => { if (event.key === "Enter") onOpen(session); }}
                aria-label={`${baseOf(session.symbol)} 종료 기록 열기`}
              >
                <CoinIcon symbol={session.symbol} size={32} className="agent-history-coin" alt="" />
                <span className="agent-history-symbol" role="cell">
                  <b className="num">{baseOf(session.symbol)}</b><small className="num">{quoteOf(session.symbol)}</small>
                  <span className="agent-history-env">{environmentLabel(session)}</span>
                </span>
                <span className="agent-history-strategy" role="cell" title={strategyLine(session)}>{strategyLine(session)}</span>
                <span className="agent-history-when" role="cell">
                  <b>{duration ? `${duration} 실행` : "—"}</b>
                  <span>{runPeriodLabel(session.started_at, session.stopped_at)}</span>
                </span>
                <span className={`agent-history-end is-${end.tone}`} role="cell"><i aria-hidden="true" />{end.text}</span>
                <span className={`agent-history-pnl num is-${tone}`} role="cell">{formatSignedMoney(session.realized_pnl, session.symbol)}</span>
                <span className="agent-history-pin-cell" role="cell">
                  <button
                    type="button"
                    className={`agent-history-pin${session.pinned ? " is-on" : ""}`}
                    aria-pressed={!!session.pinned}
                    aria-label={session.pinned ? "보관 풀기" : "보관하기"}
                    title={session.pinned ? "보관 중 — 자동 정리에서 빠져 있어요. 누르면 풀어요." : `보관하기 — ${keep}건·${days}일 자동 정리에서 빼요 (${pinLimit}건까지)`}
                    disabled={pinBusy === session.session_id}
                    onClick={(event) => { event.stopPropagation(); onTogglePin(session); }}
                  >
                    <PinIcon />
                  </button>
                </span>
                {end.tone === "warn" && session.note ? <span className="agent-history-note" role="cell">{session.note}</span> : null}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
