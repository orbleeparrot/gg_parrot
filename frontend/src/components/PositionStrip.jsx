// 내 에이전트 · 포지션 스트립 — 실행 바 아래 한 줄. 왼쪽에 큰 평가손익(%)과 금액, 가운데는
// 라벨 왼쪽·숫자 오른쪽의 두 열 표(평단·수량·현재가·실행 시간·실현손익·청산 기준), 오른쪽은
// 손절↔익절 사이 어디쯤인지 보여 주는 게이지. 게이지의 양 끝은 실행 중인 매크로의 청산 규칙에서
// 읽고(positionExits.js), 없는 쪽은 없다고 표시하며, 규칙이 아예 없으면 게이지 대신 글 한 줄.
import { useEffect, useState } from "react";
import { fmtPrice, fmtQty } from "../lib/format.js";
import {
  exitRules, fmtSignedMoney, fmtSignedPct, gaugeModel, runningFor, toneOf, unrealizedMoney,
} from "../lib/positionExits.js";

function useMinuteTick() {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 60_000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function Gauge({ model, pct }) {
  return (
    <div className="agent-gauge" role="img" aria-label={`평가손익 ${fmtSignedPct(pct)} · ${model.left.label} 에서 ${model.right.label} 사이`}>
      <div className={"agent-gauge-bar" + (model.oneSided ? " is-one-sided" : "")}>
        <i style={{ left: `${Math.round(model.position * 1000) / 10}%` }} />
      </div>
      <div className="agent-gauge-ends">
        <span className={model.left.tone}>{model.left.label}</span>
        <span>평단</span>
        <span className={model.right.tone}>{model.right.label}</span>
      </div>
    </div>
  );
}

export default function PositionStrip({ session, macro }) {
  const now = useMinuteTick();
  if (!session) return null;
  const running = session.status === "running";
  const inPosition = running && Boolean(session.in_position);
  const rules = exitRules(macro, session.position_side);
  const pct = Number(session.unrealized_pct ?? 0);
  const money = inPosition ? unrealizedMoney(session) : null;
  const gauge = inPosition ? gaugeModel(pct, rules) : { show: false };
  const realized = Number(session.realized_pnl ?? 0);
  const label = inPosition ? "평가손익" : running ? "무포지션 · 대기 중" : session.status === "error" ? "실행 오류" : "실행 종료";
  const elapsed = runningFor(session.started_at, now);

  return (
    <section className={"agent-position" + (inPosition ? " is-open" : "")} aria-label="포지션 요약">
      <div className="agent-position-main">
        <span className="agent-position-label">{label}</span>
        {inPosition ? (
          <span className={"agent-position-pct num " + toneOf(pct)}>
            {fmtSignedPct(pct)}
            {money !== null ? <small className="num">{fmtSignedMoney(money, session.symbol)}</small> : null}
          </span>
        ) : (
          <span className="agent-position-idle">
            실현손익 누적 <b className={"num " + toneOf(realized)}>{fmtSignedMoney(realized, session.symbol)}</b>
          </span>
        )}
      </div>
      <dl className="agent-position-table">
        <div><dt>평단</dt><dd className="num">{inPosition ? fmtPrice(session.entry_price) : "—"}</dd></div>
        <div><dt>수량</dt><dd className="num">{inPosition ? fmtQty(session.position_qty, session.symbol) : "—"}</dd></div>
        <div><dt>현재가</dt><dd className="num">{Number(session.last_price) > 0 ? fmtPrice(session.last_price) : "—"}</dd></div>
        <div><dt>{running ? "실행 시간" : "실행했던 시간"}</dt><dd className="num">{elapsed || "—"}</dd></div>
        <div><dt>실현손익 · 누적</dt><dd className={"num " + toneOf(realized)}>{fmtSignedMoney(realized, session.symbol)}</dd></div>
        <div><dt>청산 기준</dt><dd>{rules.summary}</dd></div>
      </dl>
      <div className="agent-position-gauge">
        {gauge.show ? <Gauge model={gauge} pct={pct} /> : (
          <span className="agent-position-gauge-none">
            {inPosition ? "청산 규칙 없음 · 전략 신호로 청산" : running ? "포지션이 열리면 손절·익절까지의 거리를 보여 줘요" : ""}
          </span>
        )}
      </div>
    </section>
  );
}
