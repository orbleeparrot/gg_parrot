// 실시간 토스트 — 새 알림이 오면 헤더 바로 아래 오른쪽 위에 스르륵 나타났다가 5초 뒤 스르륵 사라진다.
// 모양은 알림 패널의 행과 같고(아이콘 · 종류/시각 · 제목 · 본문 · 포인트), 아래의 가는 막대가
// 남은 시간만큼 줄어든다. 마우스를 올리면 막대와 시간이 멈추고, 떼면 이어진다.
// 최신이 위에 오고 3개까지만 쌓인다. 누르면 알림이 가리키는 화면으로, ✕ 로 바로 닫는다.
// 헤더는 backdrop-filter 를 쓰므로 그 안의 fixed 요소는 헤더에 갇힌다 — body 로 포털한다.
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { kindLabel, pointsOf } from "../lib/notifications.js";
import NotificationKindIcon from "./notificationKindIcon.jsx";

export const TOAST_HOLD_MS = 5000;

function useHeaderBottom() {
  const [top, setTop] = useState(64);
  useEffect(() => {
    const measure = () => {
      const header = document.querySelector(".site-header");
      if (header) setTop(Math.round(header.getBoundingClientRect().bottom) + 8);
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  return top;
}

export default function NotificationToasts({ toasts, onDismiss, onOpen }) {
  const top = useHeaderBottom();
  if (typeof document === "undefined") return null;
  return createPortal(
    <div className="ggp-toasts" style={{ top }} role="status" aria-live="polite" aria-label="새 알림">
      {toasts.map((toast) => (
        <Toast key={toast.id} toast={toast} onDismiss={onDismiss} onOpen={onOpen} />
      ))}
    </div>,
    document.body,
  );
}

function Toast({ toast, onDismiss, onOpen }) {
  const [leaving, setLeaving] = useState(false);
  const { item } = toast;
  const points = pointsOf(item);
  const leave = () => setLeaving(true);
  const onAnimationEnd = (event) => {
    // 막대가 다 줄면 나가고, 나가는 애니메이션이 끝나면 목록에서 지운다.
    if (event.animationName === "ggp-toast-drain") leave();
    else if (event.animationName === "ggp-toast-out") onDismiss(toast.id);
  };
  return (
    <div
      className={"ggp-toast" + (leaving ? " is-leaving" : "")}
      onAnimationEnd={onAnimationEnd}
      onClick={() => { onOpen(item); leave(); }}
      role="button"
      tabIndex={0}
      onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(item); leave(); } }}
    >
      <span className="ggp-toast-icon" aria-hidden="true"><NotificationKindIcon kind={item.kind} /></span>
      <span className="ggp-toast-text">
        <span className="ggp-toast-meta t-caption">
          <span>{kindLabel(item.kind)}</span>
          <span>방금 전</span>
        </span>
        <span className="ggp-toast-title">{item.title}</span>
        {item.body ? <span className="ggp-toast-body">{item.body}</span> : null}
      </span>
      <span className="ggp-toast-side">
        <button
          type="button"
          className="ggp-toast-close"
          aria-label="알림 닫기"
          onClick={(event) => { event.stopPropagation(); leave(); }}
        >
          ×
        </button>
        {points ? <b className="ggp-toast-points num">+{points.toLocaleString("ko-KR")} P</b> : null}
      </span>
      <span className="ggp-toast-bar" aria-hidden="true"><i /></span>
    </div>
  );
}
