// 내 에이전트 · 포지션 블록 — 오른쪽 열(에이전트 기록) 맨 위. 첫 줄은 상태 점 + 큰 평가손익(%)·금액과
// 오른쪽의 손절↔익절 게이지, 둘째 줄은 라벨 왼쪽·숫자 오른쪽의 두 열 표(평단·수량 / 현재가·실행 시간 /
// 실현손익·실행기 / 청산 기준). 게이지의 양 끝은 실행 중인 매크로의 청산 규칙에서 읽고(positionExits.js),
// 없는 쪽은 없다고 표시하며, 규칙이 아예 없으면 게이지 대신 글 한 줄. 열 너비에 맞춰 게이지가 아래로 접힌다.
import { useEffect, useState } from "react";
import { fmtPrice, fmtQty } from "../lib/format.js";
import {
  exitRules, fmtSignedMoney, fmtSignedPct, gaugeModel, headlineReturn, runningFor, toneOf, unrealizedMoney,
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

// 실행 바에서 옮겨 온 상태 문구 — 상태 점 색과 함께 블록의 라벨이 된다.
export function positionState(session) {
  const running = session?.status === "running";
  if (session?.position_uncertain) return { label: "포지션 확인 필요", dot: "is-checking" };
  if (session?.status === "error") return { label: "실행 오류", dot: "is-off" };
  if (!running) return { label: "실행 종료", dot: "is-off" };
  if (session.stopping) return { label: "종료 처리 중…", dot: "is-checking" };
  if (!session.connected) return { label: "응답 확인 중", dot: "is-checking" };
  if (session.in_position) return { label: headlineReturn(session).label, dot: "is-running" };
  return { label: "무포지션 · 대기 중", dot: "is-running" };
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
  const headline = headlineReturn(session);
  const state = positionState(session);
  const elapsed = runningFor(session.started_at, now);
  const runner = [session.runner_version ? `v${session.runner_version}` : "", session.macro_origin_label || ""].filter(Boolean).join(" · ");

  return (
    <section className={"agent-position" + (inPosition ? " is-open" : "")} aria-label="포지션 요약">
      <div className="agent-position-head">
        <div className="agent-position-main">
          <span className="agent-position-label">
            <i className={`agent-live-dot ${state.dot}`} aria-hidden="true" />
            {state.label}
          </span>
          {inPosition ? (
            <span className={"agent-position-pct num " + toneOf(headline.pct)}>
              {fmtSignedPct(headline.pct)}
              {money !== null ? <small className="num">{fmtSignedMoney(money, session.symbol)}</small> : null}
              {headline.note ? <small className="agent-position-note">{headline.note}</small> : null}
            </span>
          ) : headline.note ? (
            <span className="agent-position-idle">
              {headline.label} <b className={"num " + toneOf(headline.pct)}>{fmtSignedPct(headline.pct)}</b>
              <small className="agent-position-note">{headline.note}</small>
            </span>
          ) : (
            <span className="agent-position-idle">
              실현손익 누적 <b className={"num " + toneOf(realized)}>{fmtSignedMoney(realized, session.symbol)}</b>
            </span>
          )}
        </div>
        <div className="agent-position-gauge">
          {gauge.show ? <Gauge model={gauge} pct={pct} /> : (
            <span className="agent-position-gauge-none">
              {inPosition ? "청산 규칙 없음 · 전략 신호로 청산" : running ? "포지션이 열리면 손절·익절까지의 거리를 보여 줘요" : ""}
            </span>
          )}
        </div>
      </div>
      <dl className="agent-position-table">
        <div><dt>평단</dt><dd className="num">{inPosition ? fmtPrice(session.entry_price) : "—"}</dd></div>
        <div><dt>수량</dt><dd className="num">{inPosition ? fmtQty(session.position_qty, session.symbol) : "—"}</dd></div>
        <div><dt>현재가</dt><dd className="num">{Number(session.last_price) > 0 ? fmtPrice(session.last_price) : "—"}</dd></div>
        <div><dt>{running ? "실행 시간" : "실행했던 시간"}</dt><dd className="num">{elapsed || "—"}</dd></div>
        <div><dt>실현손익 · 누적</dt><dd className={"num " + toneOf(realized)}>{fmtSignedMoney(realized, session.symbol)}</dd></div>
        <div><dt>투입금</dt><dd className="num">{Number(session.invested_usdt) > 0 ? `${Number(session.invested_usdt).toLocaleString("en-US", { maximumFractionDigits: 2 })} USDT` : "—"}</dd></div>
        <div><dt>실행기</dt><dd className="num">{runner || "—"}</dd></div>
        <div className="is-wide"><dt>청산 기준</dt><dd>{rules.summary}</dd></div>
      </dl>
    </section>
  );
}
