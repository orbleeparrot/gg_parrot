import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CopyIcon } from "@phosphor-icons/react/dist/csr/Copy";
import { CheckIcon } from "@phosphor-icons/react/dist/csr/Check";
import "./RunnerDeviceHandoff.css";

export default function RunnerDeviceHandoff({ installation = false, isMobile = true, onExit }) {
  const [copyState, setCopyState] = useState("idle");
  const addressRef = useRef(null);
  const Heading = installation ? "h2" : "h1";
  // The handoff contains a public page address, never member keys or launch tickets.
  const address = new URL(installation ? "/runner/install" : "/?run=1&step=1", window.location.origin).href;

  async function copyAddress() {
    try {
      await navigator.clipboard.writeText(address);
      setCopyState("copied");
    } catch (_) {
      setCopyState("manual");
      window.requestAnimationFrame(() => {
        addressRef.current?.focus();
        addressRef.current?.select();
      });
    }
  }

  return (
    <section className="runner-device-handoff" aria-labelledby="runner-device-title">
      {!installation ? (
        <img className="runner-device-mascot" src="/brand/navigation/ggparrot-nav-agent.svg" alt="" width="96" height="96" draggable="false" />
      ) : null}
      <Heading id="runner-device-title">실행은 Windows PC에서</Heading>
      <p>
        실행기는 Windows 10 이상에서 사용할 수 있어요.
        {isMobile ? " 모바일" : " 웹"}에서는 매크로를 만들고, 실행 중인 에이전트의 상태와 손익을 확인할 수 있어요.
      </p>
      <button type="button" className="btn btn-l btn-primary runner-device-copy" onClick={() => void copyAddress()}>
        {copyState === "copied" ? <CheckIcon size={20} aria-hidden="true" /> : <CopyIcon size={20} aria-hidden="true" />}
        {copyState === "copied" ? "주소 복사됨" : installation ? "PC 설치 주소 복사" : "PC 실행 주소 복사"}
      </button>
      <span className="sr-only" role="status">{copyState === "copied" ? "Windows PC에서 열 주소를 복사했어요." : ""}</span>
      {copyState === "manual" ? (
        <div className="runner-device-address">
          <label htmlFor="runner-device-address">주소를 복사해 Windows PC에서 여세요.</label>
          <input id="runner-device-address" ref={addressRef} value={address} readOnly onFocus={(event) => event.target.select()} />
        </div>
      ) : null}
      <nav className="runner-device-links" aria-label="이 기기에서 할 수 있는 일">
        <Link to="/builder" className="btn btn-m btn-secondary">직접 만들기</Link>
        <Link to="/agents" className="btn btn-m btn-secondary">내 에이전트</Link>
      </nav>
      {!installation ? <p className="runner-device-note">Windows PC에서 같은 계정으로 로그인하면 저장한 매크로를 이어서 실행할 수 있어요.</p> : null}
      {onExit ? <button type="button" className="runner-device-back" onClick={onExit}>시작 화면으로</button> : null}
    </section>
  );
}
