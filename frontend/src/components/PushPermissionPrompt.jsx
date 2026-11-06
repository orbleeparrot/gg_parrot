// 앱(홈 화면 · 바탕화면)으로 처음 열었을 때 한 번 묻는 '알림 받을까요?' (시안 ①, 11/06 확정).
// 로그인한 채 앱으로 열었고, 이 기기에서 알림이 꺼져 있고, 브라우저 권한을 아직 묻지 않았을 때만. 답하면(받기 · 나중에)
// 다시 묻지 않는다 — 그 뒤로는 내 에이전트 '알림' 단추와 설정 › 앱 알림에서 켠다. 권한 창은 '알림 받기'를 누른 뒤에만 뜬다.
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import AppToast from "./AppToast.jsx";
import { useModalLayer } from "../hooks/useModalLayer.js";
import { enableToast, usePushState } from "../hooks/usePushState.js";
import { isStandalone } from "../lib/pwa.js";
import { shouldAskPush } from "../lib/push.js";
import "./PushPermissionPrompt.css";

const ASKED_KEY = "ggp_push_asked";

function asked() {
  try {
    return Boolean(globalThis.localStorage?.getItem(ASKED_KEY));
  } catch {
    return true; // 저장이 막힌 브라우저에서 매번 묻지 않는다
  }
}

function markAsked() {
  try {
    globalThis.localStorage?.setItem(ASKED_KEY, String(Date.now()));
  } catch {
    /* 저장이 막혀도 이번 실행에서는 다시 안 뜬다 */
  }
}

export default function PushPermissionPrompt() {
  const push = usePushState();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    const permission = globalThis.Notification?.permission;
    if (open || !shouldAskPush({ standalone: isStandalone(), state: push.state, permission, alreadyAsked: asked(), path: pathname })) {
      return undefined;
    }
    // 화면이 자리 잡은 뒤 — 다른 대화상자(개발 노트 등)가 떠 있으면 이번에는 건너뛰고 다음 화면 이동 때 다시 본다.
    const timer = window.setTimeout(() => {
      if (!document.querySelector('[aria-modal="true"]')) setOpen(true);
    }, 1500);
    return () => window.clearTimeout(timer);
  }, [open, push.state, pathname]);

  function later() {
    markAsked();
    setOpen(false);
  }

  function accept() {
    markAsked();
    push.enable().then((result) => {
      setOpen(false);
      setToast(enableToast(result));
    });
  }

  return (
    <>
      {open ? <AskDialog busy={push.busy} onAccept={accept} onLater={later} /> : null}
      {toast ? <AppToast key={toast.title} {...toast} onClose={() => setToast(null)} /> : null}
    </>
  );
}

function AskDialog({ busy, onAccept, onLater }) {
  const titleId = useId();
  const ref = useRef(null);
  const acceptRef = useRef(null);
  useModalLayer({ open: true, ref, onEscape: onLater, busy, trap: true, initialFocusRef: acceptRef });
  return createPortal(
    <div className="scrim fixed inset-0 z-90 grid place-items-center p-4">
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={titleId} className="dialog confirm-dialog">
        <img src="/brand/agent/ggparrot-agent-curious-v1.svg" alt="" width="64" height="64" className="push-ask-face" draggable="false" />
        <h2 id={titleId} className="t-h4 text-slate-900">코인 소식, 알림으로 받을까요?</h2>
        <div className="push-ask-sample" role="img" aria-label="알림 예시: LTC · 관련 기사가 나왔어요, 라이트코인 ETF 심사 연기">
          <img src="/favicon/android-icon-192x192.png" alt="" width="36" height="36" />
          <span className="push-ask-sample-text" aria-hidden="true">
            <span className="push-ask-sample-meta">껄무새 · 지금</span>
            <b>LTC · 관련 기사가 나왔어요</b>
            <span>라이트코인 ETF 심사 연기</span>
          </span>
        </div>
        <div className="confirm-dialog-actions">
          <button ref={acceptRef} type="button" className="btn btn-l w-full btn-primary" disabled={busy} onClick={onAccept}>
            {busy ? "켜는 중…" : "알림 받기"}
          </button>
          <button type="button" className="btn btn-l w-full btn-ghost" disabled={busy} onClick={onLater}>나중에</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
