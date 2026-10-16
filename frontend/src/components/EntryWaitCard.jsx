// 진입 대기 카드 — 진입을 기다리는 매크로가 포지션 없이 돌고 있을 때, 손절·익절 게이지 자리에 놓인다.
// 위계는 글자 크기 · 굵기로만: 상태 한 줄 → 조건 한 문장(크게) → 지금과의 거리 → 막대 → 세 칸(라벨 위 값) → 확인 기록.
// 숫자는 lib/entryWait.js 가 공개 캔들로 서버 판정을 다시 계산한 것이다.
import { fmtRoughSpan } from "../lib/entryWait.js";
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

export default function EntryWaitCard({ model }) {
  if (!model) return null;
  const typical = model.typical;
  return (
    <section className={"ewc" + (model.reached ? " is-reached" : "")} aria-label="진입 대기">
      <p className="ewc-state">
        <i className="agent-live-dot is-running" aria-hidden="true" />
        <b>진입 대기</b>{model.waitingFor ? <span className="num">{model.waitingFor}째</span> : null}
      </p>
      <p className="ewc-sentence">{model.sentence}</p>
      {model.distance ? <p className="ewc-distance">{model.distance}</p> : null}
      <Meter meter={model.meter} />
      <dl className="ewc-facts">
        <div>
          <dt>다음 확인</dt>
          <dd className="num">{model.nextCheck}</dd>
          {model.nextCheckIn ? <dd className="ewc-fact-sub">{model.nextCheckIn}</dd> : null}
        </div>
        <div>
          <dt>평소 기다린 시간</dt>
          <dd className="num">{typical && typical.avgMs ? fmtRoughSpan(typical.avgMs) : typical ? "드묾" : "—"}</dd>
          {typical ? <dd className="ewc-fact-sub">최근 {fmtRoughSpan(typical.spanMs).replace("약 ", "")} 동안 {typical.episodes}번</dd> : null}
        </div>
        <div>
          <dt>지금까지 확인</dt>
          <dd className="num">{model.checks === null ? "계속" : `${model.checks}번`}</dd>
          <dd className="ewc-fact-sub">{model.realtime ? "3초마다" : `${model.intervalLabel} 봉 마감마다`}</dd>
        </div>
      </dl>
      {model.log.length ? (
        <ol className="ewc-log" aria-label="확인 기록">
          {model.log.map((row) => (
            <li key={row.at} className={row.hit ? "is-hit" : undefined}>
              <time className="num">{row.at}</time>
              <span className="num">{row.text}</span>
              <b>{row.hit ? "조건 맞음" : "아직"}</b>
            </li>
          ))}
          {model.startedAt ? (
            <li className="is-start"><time className="num">{model.startedAt}</time><span>실행 시작</span><b /></li>
          ) : null}
        </ol>
      ) : null}
    </section>
  );
}
