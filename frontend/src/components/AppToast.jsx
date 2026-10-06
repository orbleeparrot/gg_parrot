// 한 번 알리고 사라지는 앱 토스트 — 알림 토스트(NotificationToasts)와 같은 판·위치·5초 막대.
// 서버 알림이 아니라 방금 한 일의 결과(등록 완료 등)를 알릴 때 쓴다. 페이지 안 배너 대신 이것을 쓴다.
import { useState } from "react";
import { createPortal } from "react-dom";
import { useToastHost } from "./NotificationToasts.jsx";
import { Icon } from "./icons.jsx";

export default function AppToast({ title, body = "", label = "완료", onClose }) {
  const host = useToastHost();
  const [leaving, setLeaving] = useState(false);
  if (!host) return null;
  const onAnimationEnd = (event) => {
    if (event.animationName === "ggp-toast-drain") setLeaving(true);
    else if (event.animationName === "ggp-toast-out") onClose?.();
  };
  return createPortal(
    <div className={"ggp-toast" + (leaving ? " is-leaving" : "")} onAnimationEnd={onAnimationEnd}>
      <span className="ggp-toast-icon is-done" aria-hidden="true"><Icon name="check" size={20} /></span>
      <span className="ggp-toast-text">
        <span className="ggp-toast-meta t-caption"><span>{label}</span><span>방금 전</span></span>
        <span className="ggp-toast-title">{title}</span>
        {body ? <span className="ggp-toast-body">{body}</span> : null}
      </span>
      <span className="ggp-toast-side">
        <button type="button" className="ggp-toast-close" aria-label="알림 닫기" onClick={() => setLeaving(true)}>
          <Icon name="x" size={16} />
        </button>
      </span>
      <span className="ggp-toast-bar" aria-hidden="true"><i /></span>
    </div>,
    host,
  );
}
