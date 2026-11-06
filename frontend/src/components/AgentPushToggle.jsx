// 내 에이전트 채팅 탭 줄 끝의 '알림' 단추 — 이 기기의 앱 알림을 켜고 끈다(시안 ②, 11/06 확정).
// 앱으로 열지 않은 아이폰 · 앱 안 브라우저는 누르면 설치 안내가 뜬다. PC(실행기가 알림을 보냄) · 알림을 쓸 수 없는
// 브라우저 · 서버면 단추가 없다.
import { useState } from "react";
import { BellIcon } from "@phosphor-icons/react/dist/csr/Bell";
import { BellRingingIcon } from "@phosphor-icons/react/dist/csr/BellRinging";
import AppToast from "./AppToast.jsx";
import { enableToast, usePushState } from "../hooks/usePushState.js";
import { openAppInstall } from "../lib/installDialog.js";
import "./AgentPushToggle.css";

const SHOWN = new Set(["on", "off", "denied", "needs-install", "in-app"]);

export default function AgentPushToggle() {
  const push = usePushState();
  const [toast, setToast] = useState(null);
  if (!push.signedIn || !SHOWN.has(push.state)) return null;
  const on = push.state === "on";

  function onClick() {
    if (push.state === "needs-install" || push.state === "in-app") {
      openAppInstall();
      return;
    }
    if (on) {
      push.disable().then(() => setToast({ tone: "done", label: "알림", title: "앱 알림을 껐어요" }));
      return;
    }
    push.enable().then((result) => setToast(enableToast(result)));
  }

  return (
    <div className="agent-push-toggle">
      <button
        type="button"
        aria-pressed={on}
        aria-label={on ? "앱 알림 끄기" : "앱 알림 켜기"}
        title={on ? "이 기기에서 앱 알림을 받는 중" : "이 기기에서 앱 알림 받기"}
        className={on ? "is-on" : ""}
        disabled={push.busy}
        onClick={onClick}
      >
        {on ? <BellRingingIcon size={18} weight="fill" aria-hidden="true" /> : <BellIcon size={18} weight="bold" aria-hidden="true" />}
        <span>알림</span>
      </button>
      {toast ? <AppToast key={toast.title} {...toast} onClose={() => setToast(null)} /> : null}
    </div>
  );
}
