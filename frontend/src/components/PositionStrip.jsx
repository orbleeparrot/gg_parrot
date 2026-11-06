// 내 에이전트 · 포지션 블록 — 오른쪽 열(에이전트 기록) 맨 위. 첫 줄은 상태 점 + 큰 평가손익(%)·금액과
// 오른쪽의 손절↔익절 게이지, 둘째 줄은 라벨 왼쪽·숫자 오른쪽의 두 열 표(평단·수량 / 현재가·실행 시간 /
// 실현손익·실행기 / 청산 기준). 게이지의 양 끝은 실행 중인 매크로의 청산 규칙에서 읽고(positionExits.js),
// 없는 쪽은 없다고 표시하며, 규칙이 아예 없으면 게이지 대신 글 한 줄. 열 너비에 맞춰 게이지가 아래로 접힌다.
import { useEffect, useState } from "react";
import { fmtPrice, fmtQty } from "../lib/format.js";
import { formatQuoteAmount } from "../lib/exchanges.js";
import { useRunnerDownload } from "../lib/runnerDownload.js";
import EntryWaitCard from "./EntryWaitCard.jsx";
import { Icon } from "./icons.jsx";
import {
  GROSS_TAG, exitRules, fmtSignedMoney, fmtSignedPct, gaugeModel, headlineReturn, realizedPnl, runningFor, toneOf,
} from "../lib/positionExits.js";

// 평단 · 수량 · 실행기 같은 세부 정보는 처음엔 접어 둔다 — 채팅(껄무새 중계)이 오른쪽 열을 넓게 쓰게.
// 펼친 상태는 이 브라우저에만 기억한다(개인 편의).
const DETAILS_KEY = "ggp.agent.position.details";

function useDetailsOpen(initial = false) {
  const [open, setOpen] = useState(() => {
    try {
      const stored = window.localStorage.getItem(DETAILS_KEY);
      return stored ? stored === "open" : initial;
    } catch {
      return initial;
    }
  });
  function toggle() {
    setOpen((current) => {
      const next = !current;
      try { window.localStorage.setItem(DETAILS_KEY, next ? "open" : "closed"); } catch { /* 저장소를 못 쓰면 이 화면에서만 */ }
      return next;
    });
  }
  return [open, toggle];
}

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
    <div className="agent-gauge" role="img" aria-label={`평단 대비 ${fmtSignedPct(pct)} · ${model.left.label} 에서 ${model.right.label} 사이`}>
      <div className={"agent-gauge-bar" + (model.oneSided ? " is-one-sided" : "")}>
        <i style={{ "--at": `${Math.round(model.position * 1000) / 10}%` }} />
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

export default function PositionStrip({ session, macro, waitModel = null, defaultDetailsOpen = false }) {
  const now = useMinuteTick();
  const download = useRunnerDownload();
  const [detailsOpen, toggleDetails] = useDetailsOpen(defaultDetailsOpen);
  if (!session) return null;
  const running = session.status === "running";
  const inPosition = running && Boolean(session.in_position);
  const rules = exitRules(macro, session.position_side);
  // 게이지(손절 · 익절까지의 거리)는 평단 대비 가격, 큰 숫자는 실행기와 같은 수수료 반영 평가손익(headlineReturn).
  const pct = Number(session.unrealized_pct ?? 0);
  const gauge = inPosition ? gaugeModel(pct, rules) : { show: false };
  const realizedView = realizedPnl(session);
  const realized = realizedView.money;
  const realizedTag = !realizedView.feesIncluded && realized !== 0 ? ` · ${GROSS_TAG}` : "";
  const headline = headlineReturn(session);
  const money = inPosition ? headline.money : null;
  const state = positionState(session);
  const elapsed = runningFor(session.started_at, now);
  const runner = [session.runner_version ? `v${session.runner_version}` : "", session.macro_origin_label || ""].filter(Boolean).join(" · ");
  // 이 세션을 돌리는 실행기보다 새 공식 버전이 있으면 그 자리에서 받게 한다(실행기는 버전을 보낸 v7+ 만).
  const newer = /^\d+$/.test(String(session.runner_version || "")) && /^\d+$/.test(String(download.version || ""))
    && Number(download.version) > Number(session.runner_version) && !!download.url;

  // 진입을 기다리는 매크로가 아직 안 샀으면 머리 줄(상태 · 실현손익 · 게이지 안내) 대신 진입 대기 카드를 둔다.
  const waiting = running && !inPosition && Boolean(waitModel);

  return (
    <section className={"agent-position" + (inPosition ? " is-open" : "")} aria-label="포지션 요약">
      {waiting ? <EntryWaitCard model={waitModel} /> : (
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
              실현손익 누적 <b className={"num " + toneOf(realized, session.symbol)}>{fmtSignedMoney(realized, session.symbol)}</b>{realizedTag}
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
      )}
      <button type="button" className="agent-position-toggle" aria-expanded={detailsOpen}
        aria-controls={`agent-position-details-${session.session_id}`} onClick={toggleDetails}>
        <span>{detailsOpen ? "자세히 접기" : "평단 · 수량 · 실행기 자세히"}</span>
        {!detailsOpen && newer ? <b className="num">새 실행기 v{download.appVersion || download.version}</b> : null}
        <Icon name={detailsOpen ? "chevronUp" : "chevronDown"} size={14} className="ui-icon-inline" />
      </button>
      {detailsOpen ? (
      <dl className="agent-position-table" id={`agent-position-details-${session.session_id}`}>
        <div><dt>평단</dt><dd className="num">{inPosition ? fmtPrice(session.entry_price) : "—"}</dd></div>
        <div><dt>수량</dt><dd className="num">{inPosition ? fmtQty(session.position_qty, session.symbol) : "—"}</dd></div>
        <div><dt>현재가</dt><dd className="num">{Number(session.last_price) > 0 ? fmtPrice(session.last_price) : "—"}</dd></div>
        <div><dt>{running ? "실행 시간" : "실행했던 시간"}</dt><dd className="num">{elapsed || "—"}</dd></div>
        <div><dt>실현손익 · 누적</dt><dd className={"num " + toneOf(realized, session.symbol)}>{fmtSignedMoney(realized, session.symbol)}{realizedTag}</dd></div>
        <div><dt>투입금</dt><dd className="num">{Number(session.invested_usdt) > 0 ? formatQuoteAmount(session.invested_usdt, session.symbol) : "—"}</dd></div>
        <div><dt>실행기</dt><dd className="num">{runner || "—"}</dd></div>
        {newer ? (
          <div className="is-wide"><dt>새 실행기</dt><dd>
            <a className="agent-runner-update num" href={download.url} target={download.isExternal ? "_blank" : undefined}
              rel={download.isExternal ? "noopener noreferrer" : undefined}>v{download.appVersion || download.version} 받기</a>
          </dd></div>
        ) : null}
        <div className="is-wide"><dt>청산 기준</dt><dd>{rules.summary}</dd></div>
      </dl>
      ) : null}
    </section>
  );
}
