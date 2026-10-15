// 웹 '매크로 만들기 가이드' — 실행기 연결 도우미가 지금 보여 주는 화면을 같은 그림 · 같은 순서로 따라 그린다.
// 실행기(v13+)가 알린 단계만 쓴다(키 · 주소는 오지 않는다). 버튼이 아니라 거울이다 — 누르는 곳은 실행기다.
import ExchangeScreen from "./ExchangeScreen.jsx";
import { Icon } from "./icons.jsx";
import { exchangeLabel } from "../lib/exchanges.js";
import { checkRowNote, helperTrack, problemHeadline, stageTitle } from "../lib/runnerHelperProgress.js";

const ROWS = [["auth", "키 인증"], ["ip", "허용 IP"], ["order", "주문 권한"]];
const FIELD_LABELS = { upbit: ["Access Key", "Secret Key"], bithumb: ["API Key", "Secret Key"], binance: ["API Key", "Secret Key"] };
// 웹은 실행기 PC 의 주소를 모른다(보내지 않는다) — 입력칸에는 가린 주소를 친다.
const MASKED_IP = "•••.•••.•••.•••";
// 바이낸스 도우미가 안내하는 사이트 이름과 '처음이에요' 아래 글 — 실행기 connection_wizard 의 SITE_NAMES · NEW_HINTS.
const BINANCE_SITES = { testnet: "바이낸스 테스트넷", demo: "바이낸스 데모", live: "바이낸스" };
const NEW_HINTS = { testnet: "GitHub 로그인 뒤 키 만들기부터", demo: "바이낸스 로그인 뒤 키 만들기부터", live: "키 만들기 · 허용 IP 까지 차례로" };

// 그림 이름 — 바이낸스는 사이트마다 화면이 달라 'binance:testnet' 처럼 고른다. 실거래 3단계(ip)는 키 카드의 Edit restrictions.
function screenFor(progress, exchange, stage) {
  if (exchange !== "binance") return { exchange, kind: stage };
  const env = progress?.env || "testnet";
  return { exchange: `binance:${env}`, kind: stage === "ip" || stage === "fix" ? "restrict" : stage };
}

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

function CheckRows({ checks, checking = false, progress = null }) {
  return (
    <ul className="rhm-list" aria-label="연결 확인">
      {ROWS.map(([row, label]) => {
        const state = checks?.[row] || "wait";
        const note = checking ? "" : checkRowNote(row, state, progress);
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
  const binanceEnv = exchange === "binance" ? progress?.env || "testnet" : "";
  const name = binanceEnv ? BINANCE_SITES[binanceEnv] : exchangeLabel(exchange);
  const stage = progress?.stage || "";
  if (!progress) {
    return <p className="rhm-quiet">실행기 화면에서 이어서 진행해요.</p>;
  }
  if (stage === "ask") {
    return (
      <div className="rhm-ask">
        <p className="rhm-title">{name} API 키를 발급 받은 적 있나요?</p>
        <div className="rhm-opts" aria-hidden="true">
          <span><b>처음이에요</b><small>{binanceEnv ? NEW_HINTS[binanceEnv] : "허용 IP 등록부터 차례로"}</small></span>
          <span><b>이미 발급 받았어요</b><small>보관해 둔 키를 넣고 바로 검사</small></span>
        </div>
      </div>
    );
  }
  if (["ip", "perm", "keys", "existing"].includes(stage)) {
    const screen = screenFor(progress, exchange, stage);
    return (
      <>
        <ExchangeScreen exchange={screen.exchange} kind={screen.kind} typedText={MASKED_IP} logo={logo} label={`${name} ${stageTitle(progress, stage)} 화면`} />
        {stage === "keys" || stage === "existing" ? <Slots exchange={exchange} slots={progress.slots} /> : null}
      </>
    );
  }
  if (stage === "manual") {
    return (
      <>
        <p className="rhm-title">{stageTitle(progress, "manual")}</p>
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
        <CheckRows checks={progress.checks} progress={progress} />
      </>
    );
  }
  if (stage === "result_fail") {
    const fix = screenFor(progress, exchange, "fix");
    return (
      <>
        <p className="rhm-title rhm-banner"><Mark kind="bad" />{problemHeadline(progress)}</p>
        <CheckRows checks={progress.checks} progress={progress} />
        {progress.problem === "ip" ? <ExchangeScreen exchange={fix.exchange} kind={fix.kind} typedText={MASKED_IP} logo={logo} label={`${name} 허용 IP 변경 화면`} /> : null}
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
