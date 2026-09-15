// 헤더의 알림 종 — 프로필 바로 왼쪽. 안 읽은 수는 빨간 배지로, 누르면 회원 키와 같은
// 대화상자에 최근 알림(개인 알림 + 공지)이 최신순으로 열린다. 행을 누르면 읽음 처리하고
// 알림이 가리키는 화면(/agents, /board/12 …)으로 이동한다. 로그인 계정에만 보인다.
import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth.js";
import useNotifications from "../hooks/useNotifications.js";
import { badgeText, isInternalLink, kindLabel, pointsOf, relativeTime } from "../lib/notifications.js";
import {
  ArrowBendUpLeftIcon, BellIcon, ChatCircleTextIcon, CoinsIcon, EnvelopeSimpleIcon,
  MegaphoneIcon, RankingIcon, RobotIcon, TrophyIcon,
} from "./utilityIcons.jsx";

const KIND_ICONS = {
  quest: TrophyIcon,
  macro_sold: CoinsIcon,
  macro_registered: RankingIcon,
  comment: ChatCircleTextIcon,
  reply: ArrowBendUpLeftIcon,
  agent: RobotIcon,
  admin: EnvelopeSimpleIcon,
  notice: MegaphoneIcon,
};

export default function NotificationBell() {
  const { token } = useAuth();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const buttonRef = useRef(null);
  const { unread, items, loading, error, load, markRead, markAll } = useNotifications(token);

  // 다른 페이지로 가면 닫고, 바깥 클릭·Esc 로도 닫는다. 열 때 목록을 새로 읽는다.
  useEffect(() => { setOpen(false); }, [pathname]);
  useEffect(() => {
    if (!open) return undefined;
    load();
    const onPointer = (event) => { if (!rootRef.current?.contains(event.target)) setOpen(false); };
    const onKey = (event) => {
      if (event.key === "Escape") { setOpen(false); buttonRef.current?.focus(); }
    };
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, load]);

  if (!token) return null;

  const badge = badgeText(unread);
  const now = Date.now();

  const toggle = () => {
    // 좁은 화면에서는 패널을 헤더 아래에 고정하므로 종의 아래 좌표를 넘긴다(CSS 변수).
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect && rootRef.current) rootRef.current.style.setProperty("--header-bell-top", `${Math.round(rect.bottom + 8)}px`);
    setOpen((value) => !value);
  };
  const onItem = (item) => {
    if (!item.read) markRead([item.id]);
    setOpen(false);
    if (isInternalLink(item.link)) navigate(item.link);
  };

  return (
    <div ref={rootRef} className="header-bell">
      <button
        ref={buttonRef}
        type="button"
        className="header-control header-bell-button"
        aria-label={unread > 0 ? `알림 · 안 읽음 ${unread}개` : "알림"}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="header-notifications"
        onClick={toggle}
      >
        <BellIcon />
        {badge ? <span className="header-bell-badge num" aria-hidden="true">{badge}</span> : null}
        <span className="header-tooltip" aria-hidden="true">알림</span>
      </button>
      {open ? (
        <section id="header-notifications" className="header-bell-popover" role="dialog" aria-label="알림">
          <div className="header-bell-head">
            <h2 className="t-title">알림</h2>
            {unread > 0 ? (
              <button type="button" className="header-bell-all" onClick={markAll}>모두 읽음</button>
            ) : null}
          </div>
          {items === null ? (
            <p className="header-bell-empty t-small text-slate-500" role="status">{error || "불러오는 중…"}</p>
          ) : items.length === 0 ? (
            <p className="header-bell-empty t-small text-slate-500">{error || "새 알림이 없어요."}</p>
          ) : (
            <ul className="header-bell-list" aria-busy={loading || undefined}>
              {items.map((item) => {
                const Icon = KIND_ICONS[item.kind] || BellIcon;
                const points = pointsOf(item);
                return (
                  <li key={item.id}>
                    <button
                      type="button"
                      className={"header-bell-item" + (item.read ? "" : " is-unread")}
                      onClick={() => onItem(item)}
                    >
                      <span className="header-bell-icon" aria-hidden="true"><Icon /></span>
                      <span className="header-bell-text">
                        <span className="header-bell-meta t-caption">
                          <span className="header-bell-kind">{kindLabel(item.kind)}</span>
                          <time dateTime={item.created_at}>{relativeTime(item.created_ms, now)}</time>
                          {!item.read ? <span className="visually-hidden">· 안 읽음</span> : null}
                        </span>
                        <span className="header-bell-title">{item.title}</span>
                        {item.body ? <span className="header-bell-body">{item.body}</span> : null}
                      </span>
                      <span className="header-bell-side">
                        {points ? <b className="header-bell-points num">+{points.toLocaleString("ko-KR")} P</b> : null}
                        {!item.read ? <span className="header-bell-dot" aria-hidden="true" /> : null}
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      ) : null}
    </div>
  );
}
