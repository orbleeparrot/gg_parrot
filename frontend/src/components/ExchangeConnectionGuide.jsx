import { lazy, Suspense, useEffect, useId, useMemo, useRef, useState } from "react";
import { domesticConnectionSteps, DOMESTIC_KEY_PAGES } from "../lib/runnerGuide.js";
import { publicConnectionGuideUrl } from "../lib/exchangeConnection.js";
import "./ExchangeConnectionGuide.css";

const ConnectionGuideQr = lazy(() => import("./ConnectionGuideQr.jsx"));
const STEP_LABELS = { prepare: "PC 준비", permissions: "권한 선택", ip: "IP 등록", keys: "키 입력·검사" };

const SCENES = {
  prepare: ["실거래할 Windows PC를 준비해요", "PC에서 공식 API 관리 화면을 열어요", "로그인과 본인 인증을 마쳐요"],
  permissions: ["자산 조회를 선택해요", "주문 조회 · 주문하기를 선택해요", "출금 권한은 선택하지 않아요"],
  ip: ["실행기 PC에서 공인 IPv4를 확인해요", "실행기의 주소 복사를 눌러요", "거래소의 허용 IP에 붙여넣어요"],
  keys: ["발급한 두 키는 실행기 창에만 입력해요", "연결 검사를 누르고 결과를 확인해요", "실거래 시작은 별도 버튼으로 결정해요"],
};

function ActionExample({ step, frame, name }) {
  return (
    <div className="exchange-connect-example" aria-label={`${name} 연결 화면 예시 · 실제 발급 화면과 다를 수 있어요`}>
      <div className="exchange-connect-example-title">{step === "keys" || step === "ip" ? "껄무새 실행기 · Windows PC" : `${name} · 공식 API 관리`}</div>
      {step === "prepare" ? (
        <ol><li data-active={frame === 0}>Windows PC 준비</li><li data-active={frame === 1}>공식 API 관리 열기</li><li data-active={frame === 2}>로그인 · 본인 인증</li></ol>
      ) : step === "permissions" ? (
        <ul><li data-active={frame === 0}>☑ 자산 조회</li><li data-active={frame === 1}>☑ 주문 조회 · 주문하기</li><li data-active={frame === 2}>☐ 출금 — 선택하지 않음</li></ul>
      ) : step === "ip" ? (
        <ol><li data-active={frame === 0}>PC 공인 IPv4 <code className="num">203.0.113.10</code> <small>예시 주소</small></li><li data-active={frame === 1}>주소 복사 →</li><li data-active={frame === 2}>{name} 허용 IP <code className="num">203.0.113.10</code></li></ol>
      ) : (
        <ol><li data-active={frame === 0}>API Key · Secret Key <code>••••••••</code></li><li data-active={frame === 1}>연결 검사 — 주문을 만들지 않는 확인</li><li data-active={frame === 2}>검사 결과 확인 → 매크로 시작</li></ol>
      )}
    </div>
  );
}

function StepVisual({ step, name }) {
  const [playing, setPlaying] = useState(false);
  const [frame, setFrame] = useState(0);
  const [reduced, setReduced] = useState(true);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => { setReduced(query.matches); setPlaying(false); };
    setReduced(query.matches);
    setPlaying(!query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!playing || reduced) return undefined;
    const timer = window.setInterval(() => setFrame((current) => (current + 1) % 3), 4000);
    return () => window.clearInterval(timer);
  }, [playing, reduced]);
  return (
    <figure className="exchange-connect-visual">
      <ActionExample step={step} frame={frame} name={name} />
      <figcaption>화면 예시 · {SCENES[step][frame]}</figcaption>
      <div className="exchange-connect-controls" aria-label="화면 예시 재생 제어">
        {!reduced ? <button type="button" className="btn btn-s btn-secondary" onClick={() => setPlaying(!playing)}>{playing ? "일시정지" : "반복 재생"}</button> : null}
        <button type="button" className="btn btn-s btn-secondary" onClick={() => { setFrame(0); setPlaying(!reduced); }}>처음부터</button>
        {[0, 1, 2].map((index) => <button key={index} type="button" className="btn btn-s btn-secondary" aria-label={`정지 화면 ${index + 1}`} aria-pressed={frame === index && !playing} onClick={() => { setPlaying(false); setFrame(index); }}>{index + 1}</button>)}
      </div>
    </figure>
  );
}

export default function ExchangeConnectionGuide({ exchange = "upbit", initialStep = "prepare", onStepChange }) {
  const steps = useMemo(() => domesticConnectionSteps(exchange), [exchange]);
  const [stepId, setStepId] = useState(() => steps.some((s) => s.id === initialStep) ? initialStep : "prepare");
  const [qrOpen, setQrOpen] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const headingRef = useRef(null);
  const copyRef = useRef(null);
  const panelId = useId();
  useEffect(() => {
    setStepId(steps.some((s) => s.id === initialStep) ? initialStep : "prepare");
    setCopyStatus("");
  }, [initialStep, steps]);
  const current = steps.find((s) => s.id === stepId) || steps[0];
  const name = exchange === "bithumb" ? "빗썸" : "업비트";
  const index = steps.indexOf(current);
  const address = publicConnectionGuideUrl({ exchange, step: current?.id });
  if (!current) return null;

  function selectStep(id) {
    setStepId(id);
    setCopyStatus("");
    onStepChange?.(id);
    window.requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
  }
  async function copyGuide() {
    try {
      await navigator.clipboard.writeText(address);
      setCopyStatus("안내 주소를 복사했어요.");
    } catch {
      setCopyStatus("주소를 직접 선택해 복사하세요.");
      window.requestAnimationFrame(() => copyRef.current?.focus());
    }
  }
  return (
    <section className="exchange-connect" aria-label={`${name} 단계별 연결 도우미`}>
      <header className="exchange-connect-heading">
        <img src={`/exchanges/${exchange}.png`} width="28" height="28" alt="" />
        <div><h2 className="t-title">{name} 연결 도우미</h2><p className="t-small">키는 내 PC 실행기에만 입력해요. 웹에는 입력란이 없어요.</p></div>
      </header>
      <nav className="exchange-connect-nav" aria-label="연결 안내 단계">
        {steps.map((s, position) => <button key={s.id} type="button" aria-current={current.id === s.id ? "step" : undefined} aria-controls={panelId} onClick={() => selectStep(s.id)}><span className="num">{position + 1}</span>{STEP_LABELS[s.id]}</button>)}
      </nav>
      <div id={panelId} className="exchange-connect-panel">
        <div className="exchange-connect-copy">
          <p className="t-caption">안내 <span className="num">{index + 1} / {steps.length}</span> · 인증 상태가 아니에요</p>
          <h3 className="t-h2" tabIndex="-1" ref={headingRef}>{current.title}</h3>
          <p className="t-body">{current.description}</p>
          <ul className="exchange-connect-checklist">{current.checklist.map((line) => <li key={line}>{line}</li>)}</ul>
          {current.action ? <a href={current.action.href} target="_blank" rel="noopener noreferrer" className="btn btn-m btn-secondary">{current.action.label} ↗</a> : null}
        </div>
        <StepVisual key={`${exchange}:${current.id}`} step={current.id} name={name} />
      </div>
      <div className="exchange-connect-footer">
        <button type="button" className="btn btn-m btn-secondary" disabled={index === 0} onClick={() => selectStep(steps[index - 1].id)}>이전 안내</button>
        {index < steps.length - 1 ? <button type="button" className="btn btn-m btn-secondary" onClick={() => selectStep(steps[index + 1].id)}>다음 안내 →</button> : <span className="t-small">마지막 확인은 실행기에서 ‘연결 검사’로 해요.</span>}
      </div>
      <p className="exchange-connect-source t-small">출금 권한은 끄세요. 출금이 차단되어도 주문 권한이 유출되면 매매 손실이 발생할 수 있어요.</p>
      <details className="exchange-connect-handoff" open={qrOpen} onToggle={(event) => setQrOpen(event.currentTarget.open)}>
        <summary>다른 기기에서 안내만 이어보기 · QR</summary>
        {qrOpen ? <div className="exchange-connect-handoff-body">
          <Suspense fallback={<p role="status">QR 준비 중…</p>}><ConnectionGuideQr address={address} /></Suspense>
          <div><p className="t-small">QR은 공개 안내만 열어요. 키·회원 정보·실행 티켓을 옮기지 않으며, API 발급과 실행은 Windows PC에서 마쳐요.</p><a href={address} className="exchange-connect-address" ref={copyRef}>{address}</a><button type="button" className="btn btn-m btn-secondary" onClick={() => void copyGuide()}>안내 주소 복사</button><p className="t-small" role="status">{copyStatus}</p></div>
        </div> : null}
      </details>
      <p className="exchange-connect-source t-caption">공식 안내 확인: {DOMESTIC_KEY_PAGES[exchange].verifiedOn} · 화면 예시는 실제 거래소 화면과 다를 수 있어요. <a href={DOMESTIC_KEY_PAGES[exchange].helpUrl} target="_blank" rel="noopener noreferrer">공식 발급 안내 ↗</a></p>
    </section>
  );
}
