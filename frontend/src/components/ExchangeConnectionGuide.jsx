import { useEffect, useId, useMemo, useRef, useState } from "react";
import { domesticConnectionSteps, DOMESTIC_KEY_PAGES } from "../lib/runnerGuide.js";
import "./ExchangeConnectionGuide.css";

const STEP_LABELS = { prepare: "실행기·IP", permissions: "공식 키 발급", keys: "키 입력·검사" };

export default function ExchangeConnectionGuide({ exchange = "upbit", initialStep = "prepare", initialKeyMode = "new", onStepChange }) {
  const [keyMode, setKeyMode] = useState(initialKeyMode === "existing" ? "existing" : "new");
  const steps = useMemo(() => domesticConnectionSteps(exchange, { existingKey: keyMode === "existing" }), [exchange, keyMode]);
  const startStep = initialKeyMode === "existing" && initialStep === "prepare" ? "keys" : initialStep;
  const [stepId, setStepId] = useState(() => steps.some((s) => s.id === startStep) ? startStep : "prepare");
  const headingRef = useRef(null);
  const panelId = useId();
  useEffect(() => {
    setStepId(["prepare", "permissions", "keys"].includes(startStep) ? startStep : "prepare");
  }, [startStep, exchange]);
  const current = steps.find((s) => s.id === stepId) || steps[0];
  const name = exchange === "bithumb" ? "빗썸" : "업비트";
  const index = steps.indexOf(current);
  if (!current) return null;

  function selectStep(id) {
    setStepId(id);
    onStepChange?.(id);
    window.requestAnimationFrame(() => headingRef.current?.focus({ preventScroll: true }));
  }
  function selectKeyMode(mode) {
    setKeyMode(mode);
    selectStep(mode === "existing" ? "keys" : "prepare");
  }
  return (
    <section className="exchange-connect" aria-label={`${name} 단계별 연결 도우미`}>
      <header className="exchange-connect-heading">
        <img src={`/exchanges/${exchange}.png`} width="28" height="28" alt="" />
        <div><h2 className="t-title">{name} 연결 도우미</h2><p className="t-small">키는 내 PC 실행기에만 입력해요. 웹에는 입력란이 없어요.</p></div>
      </header>
      <div className="exchange-connect-key-mode" role="group" aria-label="API 키 준비 방법">
        <button type="button" className="btn btn-m btn-secondary" aria-pressed={keyMode === "new"} onClick={() => selectKeyMode("new")}>새 키 발급</button>
        <button type="button" className="btn btn-m btn-secondary" aria-pressed={keyMode === "existing"} onClick={() => selectKeyMode("existing")}>기존 키 사용</button>
      </div>
      <p className="t-small exchange-connect-mode-note">{keyMode === "existing" ? "재발급 없이 가지고 있는 키로 검사해요. 권한·IP가 바뀌었다면 2번 안내에서 확인하세요." : "처음 발급한다면 실행기 PC의 IP부터 확인해요. 이미 키가 있으면 ‘기존 키 사용’을 선택하세요."}</p>
      <nav className="exchange-connect-nav" aria-label="연결 안내 단계">
        {steps.map((s, position) => <button className="t-small" key={s.id} type="button" aria-current={current.id === s.id ? "step" : undefined} aria-controls={panelId} onClick={() => selectStep(s.id)}><span className="num">{position + 1}</span>{s.id === "permissions" && keyMode === "existing" ? "권한·IP 확인" : STEP_LABELS[s.id]}</button>)}
      </nav>
      <div id={panelId} className="exchange-connect-panel">
        <div className="exchange-connect-copy">
          <p className="t-caption">안내 <span className="num">{index + 1} / {steps.length}</span> · 인증 상태가 아니에요</p>
          <h3 className="t-h2" tabIndex="-1" ref={headingRef}>{current.title}</h3>
          <p className="t-body">{current.description}</p>
          <ul className="exchange-connect-checklist t-body">{current.checklist.map((line) => <li key={line}>{line}</li>)}</ul>
          {current.action ? <a href={current.action.href} target="_blank" rel="noopener noreferrer" className="btn btn-m btn-secondary">{current.action.label}</a> : null}
        </div>
      </div>
      <div className="exchange-connect-footer">
        <button type="button" className="btn btn-m btn-secondary" disabled={index === 0} onClick={() => selectStep(steps[index - 1].id)}>이전 안내</button>
        {index < steps.length - 1 ? <button type="button" className="btn btn-m btn-secondary" onClick={() => selectStep(steps[index + 1].id)}>다음 안내</button> : <span className="t-small">새 실행기의 ‘연결 검사’로 확인해요. 시작은 별도예요.</span>}
      </div>
      <p className="exchange-connect-source t-small">출금 권한은 끄세요. 출금이 차단되어도 주문 권한이 유출되면 매매 손실이 발생할 수 있어요.</p>
      <p className="exchange-connect-source t-caption">공식 안내 확인: <span className="num">{DOMESTIC_KEY_PAGES[exchange].verifiedOn}</span> · <a href={DOMESTIC_KEY_PAGES[exchange].helpUrl} target="_blank" rel="noopener noreferrer">공식 발급 안내</a></p>
    </section>
  );
}
