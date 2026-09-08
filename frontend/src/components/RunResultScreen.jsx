import { describeRunOutcome } from "../features/agents/runOutcome.js";

const AVATARS = {
  calm: "/brand/agent/ggparrot-agent-calm-v1.svg",
  focused: "/brand/agent/ggparrot-agent-focused-v1.svg",
  warning: "/brand/agent/ggparrot-agent-warning-v1.svg",
  critical: "/brand/agent/ggparrot-agent-critical-v1.svg",
};

// 실행이 끝나면 에이전트 채팅 자리를 통째로 차지한다 — 알림 한 줄이 아니라 결과 화면.
export default function RunResultScreen({ session, onShowLog }) {
  const outcome = describeRunOutcome(session);
  return (
    <section className={`agent-result is-${outcome.tone}`} aria-label={`${session?.symbol || ""} 실행 결과`}>
      <div className="agent-result-head">
        <img src={AVATARS[outcome.avatar] || AVATARS.calm} alt="" width="72" height="72" draggable="false" decoding="async" />
        <p className="agent-result-eyebrow">
          {outcome.pending ? <i className="agent-live-dot is-checking" aria-hidden="true" /> : null}
          {outcome.eyebrow}
        </p>
        <h2 className="agent-result-title">{outcome.title}</h2>
        {outcome.detail ? <p className="agent-result-detail">{outcome.detail}</p> : null}
      </div>

      <div className="agent-result-pnl-block">
        <span className="agent-result-pnl-label">총 실현손익</span>
        <strong className={`agent-result-pnl num is-${outcome.pnl.tone}${outcome.pending ? " is-pending" : ""}`}>
          {outcome.pnl.text}
        </strong>
      </div>

      <dl className="agent-result-rows">
        {outcome.rows.map((row) => (
          <div key={row.label} className="agent-result-row">
            <dt>{row.label}</dt>
            <dd className={row.numeric ? "num" : ""}>{row.value}</dd>
          </div>
        ))}
      </dl>

      <p className="agent-result-note">
        {outcome.pending
          ? "실행기가 확정 보고를 보내면 이 화면이 결과로 바뀝니다."
          : "실현손익은 실행기가 보고한 누적값(USDT)이에요. 거래소 체결 내역과 대조해 확인하세요."}
      </p>
      {onShowLog ? (
        <div className="agent-result-actions">
          <button type="button" className="btn btn-m btn-secondary" onClick={onShowLog}>관측 기록 보기</button>
        </div>
      ) : null}
    </section>
  );
}
