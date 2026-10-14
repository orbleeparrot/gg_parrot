// 웹 '매크로 만들기 가이드' — 실행기 연결 도우미가 지금 보여 주는 화면을 같은 그림 · 같은 순서로 따라 그린다.
// 실행기(v13+)가 알린 단계만 쓴다(키 · 주소는 오지 않는다). 버튼이 아니라 거울이다 — 누르는 곳은 실행기다.
import ExchangeScreen from "./ExchangeScreen.jsx";
import { Icon } from "./icons.jsx";
import { exchangeLabel } from "../lib/exchanges.js";
import { checkRowNote, helperTrack, PROBLEM_HEADLINES } from "../lib/runnerHelperProgress.js";

const ROWS = [["auth", "키 인증"], ["ip", "허용 IP"], ["order", "주문 권한"]];
const FIELD_LABELS = { upbit: ["Access Key", "Secret Key"], bithumb: ["API Key", "Secret Key"] };
// 웹은 실행기 PC 의 주소를 모른다(보내지 않는다) — 입력칸에는 가린 주소를 친다.
const MASKED_IP = "•••.•••.•••.•••";
const STAGE_TITLES = { ip: "허용 IP 등록", perm: "권한 선택 · 키 발급", keys: "키 복사", existing: "기존 키 넣기", manual: "직접 입력" };

function Mark({ kind }) {
  if (kind === "ok") return <span className="rhm-ic is-ok" aria-hidden="true"><Icon name="check" size={12} strokeWidth={3.2} /></span>;
  if (kind === "bad") return <span className="rhm-ic is-bad" aria-hidden="true"><Icon name="x" size={12} strokeWidth={3.2} /></span>;
  if (kind === "spin") return <span className="rhm-spin" aria-hidden="true" />;
  if (kind === "wait") return <span className="rhm-ic is-wait" aria-hidden="true" />;
  return <span className="rhm-ic is-na" aria-hidden="true"><span className="rhm-dash" /></span>;
}

function Slots({ exchange, slots }) {
  const labels = FIELD_LABELS[exchange] || ["API Key", "Secret Key"];
  return (
    <ul className="rhm-list" aria-label="키 받기">
      {["access", "secret"].map((slot, index) => (
        <li key={slot}>
          <Mark kind={slots?.[slot] ? "ok" : "wait"} />
          <span>{labels[index]}</span>
          <span className={`rhm-right ${slots?.[slot] ? "is-good" : ""}`.trim()}>{slots?.[slot] ? "받음" : "복사 대기"}</span>
        </li>
      ))}
    </ul>
  );
}

function CheckRows({ checks, checking = false }) {
  return (
    <ul className="rhm-list" aria-label="연결 확인">
      {ROWS.map(([row, label]) => {
        const state = checks?.[row] || "wait";
        const note = checking ? "" : checkRowNote(row, state);
        return (
          <li key={row}>
            <Mark kind={checking ? "spin" : state === "ok" ? "ok" : state === "bad" ? "bad" : "na"} />
            <span>{label}</span>
            {note ? <span className={`rhm-right ${state === "bad" ? "is-bad" : ""}`.trim()}>{note}</span> : null}
          </li>
        );
      })}
    </ul>
  );
}

function StageBody({ progress, exchange, logo }) {
  const name = exchangeLabel(exchange);
  const stage = progress?.stage || "";
  if (!progress) {
    return <p className="rhm-quiet">실행기 화면에서 이어서 진행해요.</p>;
  }
  if (stage === "ask") {
    return (
      <div className="rhm-ask">
        <p className="rhm-title">{name} API 키를 발급 받은 적 있나요?</p>
        <div className="rhm-opts" aria-hidden="true">
          <span><b>처음이에요</b><small>허용 IP 등록부터 차례로</small></span>
          <span><b>이미 발급 받았어요</b><small>보관해 둔 키를 넣고 바로 검사</small></span>
        </div>
      </div>
    );
  }
  if (["ip", "perm", "keys", "existing"].includes(stage)) {
    return (
      <>
        <ExchangeScreen exchange={exchange} kind={stage} typedText={MASKED_IP} logo={logo} label={`${name} ${STAGE_TITLES[stage]} 화면`} />
        {stage === "keys" || stage === "existing" ? <Slots exchange={exchange} slots={progress.slots} /> : null}
      </>
    );
  }
  if (stage === "manual") {
    return (
      <>
        <p className="rhm-title">직접 입력</p>
        <Slots exchange={exchange} slots={progress.slots} />
      </>
    );
  }
  if (stage === "checking") {
    return (
      <>
        <p className="rhm-title rhm-banner"><Mark kind="spin" />연결 확인 중</p>
        <CheckRows checks={progress.checks} checking />
      </>
    );
  }
  if (stage === "result_ok") {
    return (
      <>
        <p className="rhm-title rhm-banner"><Mark kind="ok" />연결 완료</p>
        <CheckRows checks={progress.checks} />
      </>
    );
  }
  if (stage === "result_fail") {
    return (
      <>
        <p className="rhm-title rhm-banner"><Mark kind="bad" />{PROBLEM_HEADLINES[progress.problem] || "연결 확인 필요"}</p>
        <CheckRows checks={progress.checks} />
        {progress.problem === "ip" ? <ExchangeScreen exchange={exchange} kind="fix" typedText={MASKED_IP} logo={logo} label={`${name} 허용 IP 변경 화면`} /> : null}
      </>
    );
  }
  if (stage === "started") {
    return <p className="rhm-state"><Mark kind="ok" />매크로를 시작했어요</p>;
  }
  // ready — 실행기 첫 화면. 연결됐으면 [매크로 시작]만 남았다.
  return progress.connected ? (
    <p className="rhm-state"><Mark kind="ok" />{name} API 키 연결됨 · 실행기에서 매크로 시작을 눌러요</p>
  ) : (
    <p className="rhm-state is-warn"><Mark kind="na" />{name} API 키 연결 안 됨 · 실행기에서 연결 도우미를 눌러요</p>
  );
}

export default function RunnerHelperMirror({ progress, exchange, logo = "" }) {
  const track = helperTrack(progress);
  return (
    <div className="rhm" aria-live="polite">
      <div className="rhm-head"><span className="rhm-pulse" aria-hidden="true" /><h2>실행기에서 진행 중</h2></div>
      <ol className={track.length === 2 ? "rhm-track is-two" : "rhm-track"}>
        {track.map((step, index) => (
          <li key={step.label} className={`is-${step.state}`}>
            {step.state === "done" ? <Mark kind="ok" /> : <span className="rhm-n num" aria-hidden="true">{index + 1}</span>}
            {step.label}
          </li>
        ))}
      </ol>
      <div className="rhm-body"><StageBody progress={progress} exchange={exchange} logo={logo} /></div>
    </div>
  );
}
