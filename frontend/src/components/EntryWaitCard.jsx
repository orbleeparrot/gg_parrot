// 진입 대기 카드 — 진입을 기다리는 매크로가 포지션 없이 돌고 있을 때, 손절·익절 게이지 자리에 놓인다.
// 위계는 글자 크기 · 굵기로만: 상태 줄(언제 판정) → 지금 상태 한 줄(크게) → 짧은 설명 → 막대 → 조건 체크 → 한 줄 요약.
// 긴 조건 문장("가격이 … 올라가고, 마감가가 … 위면")은 늘어놓지 않는다 — 조건이 둘이면 가격 막대 하나에 두 선을 그린다.
// 봉마다의 확인 기록은 카드에 늘어놓지 않는다 — 넘었는데 안 산 봉은 중계 채팅이 이유와 함께 말한다.
// 값은 서버 entry_wait(실행기 하트비트와 같은 값)를 lib/entryWait.js 가 화면 모양으로 옮긴 것이다.
import { fmtRoughSpan } from "../lib/entryWait.js";
import { Icon } from "./icons.jsx";
import "./EntryWaitCard.css";

function Meter({ meter }) {
  if (!meter) return null;
  const at = `${Math.round(meter.progress * 1000) / 10}%`;
  return (
    <div className="ewc-meter" role="img" aria-label={`${meter.from.label} ${meter.from.value}에서 ${meter.to.label} ${meter.to.value}까지 중 지금 ${meter.now}`}>
      <div className="ewc-bar" style={{ "--at": at }}><i className="ewc-fill" /><i className="ewc-pin" /></div>
      <div className="ewc-ends">
        <span>{meter.from.label} <b className="num">{meter.from.value}</b></span>
        <span>{meter.to.label} <b className="num">{meter.to.value}</b></span>
      </div>
    </div>
  );
}

// 함께 맞아야 하는 조건(사는 선 + 이평선 필터 · 연속 봉 · 진입 필터) — 둘 이상일 때만. 맞은 것은 초록 체크, 아직은 빈 동그라미.
// 사는 선만 넘고 필터가 남았을 때 '왜 안 사지'가 여기서 보인다.
function Conditions({ rows }) {
  if (!rows || rows.length < 2) return null;
  return (
    <ul className="ewc-conds" aria-label="사는 조건">
      {rows.map((row) => (
        <li key={row.key} className={row.ok ? "is-ok" : "is-wait"}>
          <span className="ewc-cond-mark" aria-label={row.ok ? "맞음" : "아직"}>
            {row.ok ? <Icon name="check" size={14} strokeWidth={2.5} /> : null}
          </span>
          <span className="ewc-cond-label">{row.label}</span>
          {row.now ? <span className="ewc-cond-now num">{row.now}</span> : null}
        </li>
      ))}
    </ul>
  );
}

const at = (x) => `${Math.round(Math.min(1, Math.max(0, Number(x) || 0)) * 1000) / 10}%`;
// 위치는 style 의 --at 하나로 — 꼬리표는 끝에 가까울수록 안쪽으로 당겨(left X% · translateX(-X%)) 막대 밖으로 안 나간다.

// 값 막대 — 선들(사는 선 · 평균선 · 청산 기준)을 같은 눈금(왼쪽이 작은 값)에 놓고 지금 점을 찍는다.
// 사는 구역은 초록 — 올라가야 사면 선 오른쪽, 내려가야 사면(RSI · 지정가 · 눌림) 선 왼쪽.
function Ladder({ ladder }) {
  const marks = [...ladder.marks].sort((a, b) => a.pos - b.pos);
  const close = marks.length > 1 && marks[1].pos - marks[0].pos < 0.3; // 이름표가 겹치면 둘째 줄로
  return (
    <div className="ewc-ladder" role="img" aria-label={`${marks.map((m) => `${m.label} ${m.value}`).join(" · ")} 중 지금 ${ladder.now.value}`}>
      <span className="ewc-ladder-now num" style={{ "--at": at(ladder.now.pos) }}>지금 {ladder.now.value}</span>
      <div className="ewc-ladder-track">
        <i className={"ewc-ladder-zone" + (ladder.zone_side === "below" ? " is-below" : "")} style={{ "--at": at(ladder.zone) }} />
        {marks.map((m) => <i key={m.key} className={"ewc-ladder-mark" + (m.main ? " is-main" : "")} style={{ "--at": at(m.pos) }} />)}
        <i className="ewc-ladder-dot" style={{ "--at": at(ladder.now.pos) }} />
      </div>
      <div className={"ewc-ladder-labels" + (close ? " is-stacked" : "")}>
        {marks.map((m, i) => (
          <span key={m.key} className={(m.main ? "is-main" : "") + (close && i === 1 ? " is-low" : "")} style={{ "--at": at(m.pos) }}>
            {m.label} <b className="num">{m.value}</b>
          </span>
        ))}
      </div>
    </div>
  );
}

export function waitSummary(model) {
  const typical = model.typical;
  const usual = typical && typical.avgMs ? `평소 ${fmtRoughSpan(typical.avgMs)} 기다리면 조건이 왔어요`
    : typical ? "최근엔 조건이 드물게 왔어요" : "";
  return [usual, model.realtime ? "가격이 닿는 순간 바로 사요" : ""].filter(Boolean).join(" · ");
}

export default function EntryWaitCard({ model }) {
  if (!model) return null;
  return (
    <section className={"ewc" + (model.reached ? " is-reached" : "")} aria-label="진입 대기">
      <p className="ewc-state">
        <i className="agent-live-dot is-running" aria-hidden="true" />
        <b>진입 대기</b>
        {model.intervalLabel ? <span>{model.intervalLabel} 봉</span> : null}
        {model.closeText ? <span className="num">{model.closeText}</span> : null}
      </p>
      <p className="ewc-sentence">{model.headline || model.sentence}</p>
      {model.distance ? <p className="ewc-distance">{model.distance}</p> : null}
      {model.ladder ? <Ladder ladder={model.ladder} /> : <Meter meter={model.meter} />}
      <Conditions rows={model.conditions} />
      {/* 기다리는 사람에게 필요한 건 '얼마나 걸리곤 했는지'뿐 — 언제 판정되는지는 맨 윗줄에. */}
      {waitSummary(model) ? <p className="ewc-summary">{waitSummary(model)}</p> : null}
    </section>
  );
}
