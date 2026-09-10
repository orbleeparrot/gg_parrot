import { Fragment, useCallback, useEffect, useId, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "../api.js";
import useAdaptivePolling from "../hooks/useAdaptivePolling.js";
import { getAuthUser, getToken, useAuth } from "../lib/auth.js";
import { badgeLabel, chatScope, firstUnseenId, isOwnMessage, sameAuthor } from "../lib/chatBadge.js";
import { STICKERS, stickerFromText, stickerText } from "../lib/chatStickers.js";
import { macroIdsInText, splitMacroText } from "../lib/chatMacro.js";
import CoinIcon from "./CoinIcon.jsx";
import { chatUnseenCount, getChatFeed, markChatSeen, observeChat, receiveChat, receiveChatPost, setChatLoadError, visibleChatReadId } from "../lib/chatStore.js";
import UserAvatar, { AuthorAvatar } from "./UserAvatar.jsx";
import "./ChatBox.css";

const POLL_MS = 3000;
const FAB_ICON = "/brand/ggparrot-feather-terminal.svg";
const EMPTY_FACE = "/brand/agent/ggparrot-agent-curious-v1.svg";
const PLACEMENT_KEY = "chat:placement";      // 버튼을 끌어다 둔 자리(오른쪽·아래 여백 px) — 이 브라우저에만
const OPACITY_KEY = "chat:opacity";          // 시트 불투명도 0.25~1
const DRAG_THRESHOLD = 6;                    // 이보다 덜 움직이면 클릭
const EDGE = 8;                              // 화면 가장자리 최소 여백
const TOPBAR_FALLBACK = 64;
const MOBILE_PLACEMENT_QUERY = "(max-width: 767px), (max-width: 1099px) and (pointer: coarse)";

function isMobilePlacement() {
  return typeof window !== "undefined" && window.matchMedia(MOBILE_PLACEMENT_QUERY).matches;
}
function subscribeMobilePlacement(listener) {
  const query = window.matchMedia(MOBILE_PLACEMENT_QUERY);
  query.addEventListener("change", listener);
  return () => query.removeEventListener("change", listener);
}

function readStorage(key) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key, value) {
  try { window.localStorage.setItem(key, value); } catch { /* 저장 못 해도 동작엔 지장 없다 */ }
}
function loadPlacement() {
  try {
    const parsed = JSON.parse(readStorage(PLACEMENT_KEY) || "null");
    if (parsed && Number.isFinite(parsed.right) && Number.isFinite(parsed.bottom)) return { right: parsed.right, bottom: parsed.bottom };
  } catch { /* 깨진 값은 기본 자리 */ }
  return null;
}
function loadOpacity() {
  const value = Number(readStorage(OPACITY_KEY));
  return Number.isFinite(value) && value >= 0.25 && value <= 1 ? value : 1;
}
function topbarHeight() {
  const raw = getComputedStyle(document.documentElement).getPropertyValue("--site-topbar-h");
  const value = parseFloat(raw);
  return Number.isFinite(value) ? value : TOPBAR_FALLBACK;
}
// 왼쪽 한계 — 사이드바가 고정으로 깔린 화면(≥1100px)에서는 그 오른쪽 끝에서 막는다.
// 좁은 화면의 서랍은 닫혀 있으면 폭이 0 이라 자연히 화면 가장자리가 한계가 된다.
function leftBoundary() {
  const sidebar = document.querySelector(".site-sidebar");
  if (!sidebar) return EDGE;
  const rect = sidebar.getBoundingClientRect();
  const pinned = getComputedStyle(sidebar).position === "fixed" && rect.width > 0 && rect.left <= 1;
  return pinned ? rect.right + EDGE : EDGE;
}
// 버튼(과 열린 시트)이 화면 안에, 사이드바 오른쪽에 남도록 여백을 자른다.
function clampPlacement(placement, root) {
  if (!placement || !root) return placement;
  // offsetWidth/Height 를 쓴다 — 누르는 순간의 :active scale(0.97) 이 섞이면 한계가 몇 px 헐거워진다.
  const fab = root.querySelector(".chat-fab");
  const sheet = root.querySelector(".chat-sheet");
  const width = Math.max(fab?.offsetWidth || 0, sheet?.offsetWidth || 0);
  const above = sheet ? sheet.offsetHeight + 12 : 0;
  const maxRight = Math.max(EDGE, window.innerWidth - width - leftBoundary());
  const maxBottom = Math.max(EDGE, window.innerHeight - (fab?.offsetHeight || 48) - above - topbarHeight() - EDGE);
  return {
    right: Math.round(Math.min(maxRight, Math.max(EDGE, placement.right))),
    bottom: Math.round(Math.min(maxBottom, Math.max(EDGE, placement.bottom))),
  };
}

function kstClock(now = Date.now()) {
  const date = new Date(now + 9 * 60 * 60 * 1000);
  return `${String(date.getUTCHours()).padStart(2, "0")}:${String(date.getUTCMinutes()).padStart(2, "0")}`;
}

// A member switch remounts transient UI and aborts the previous member's requests.
// Feed/read state lives outside the route, in a separate cache for each account.
// 매크로 언급 카드 — 종목 로고·종목·글쓴이·전략 한 줄. 누르면 리더보드의 그 행으로 간다.
function MacroCard({ card, onClose }) {
  const goToEntry = () => {
    const row = document.getElementById(`leaderboard-entry-${card.entry_id}`);
    if (!row) return;
    onClose?.();
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("is-flash");
    window.setTimeout(() => row.classList.remove("is-flash"), 1600);
  };
  return (
    <button type="button" className="chat-macro" onClick={goToEntry} title="리더보드에서 이 매크로 보기">
      <CoinIcon symbol={card.symbol} size={28} alt="" />
      <span className="chat-macro-body">
        <span className="chat-macro-head">
          <b className="num">{card.symbol}</b>
          <span className="chat-macro-author">{card.is_ai ? "🤖 " : ""}{card.username}</span>
        </span>
        <span className="chat-macro-summary">{card.locked ? "잠긴 매크로 · 리더보드에서 언락하면 전략이 보여요" : card.human_summary || "전략 설명 없음"}</span>
      </span>
    </button>
  );
}

export default function ChatBox(props) {
  const { token, user } = useAuth();
  const member = token && user?.id != null ? user : null;
  const scope = chatScope(member?.id);
  return <MemberChatBox key={scope} {...props} member={member} scope={scope} />;
}

function MemberChatBox({ member, scope, defaultOpen = false, defaultStickerTray = false }) {
  const mobilePlacement = useSyncExternalStore(subscribeMobilePlacement, isMobilePlacement, () => false);
  const panelId = useId();
  const subscribe = useCallback((listener) => observeChat(scope, listener), [scope]);
  const snapshot = useCallback(() => getChatFeed(scope), [scope]);
  const feed = useSyncExternalStore(subscribe, snapshot, snapshot);
  const { items, loaded, seenId, loadError } = feed;
  const [open, setOpen] = useState(defaultOpen);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [readRetry, setReadRetry] = useState(0);
  const [dividerId, setDividerId] = useState(null);
  const [stickerOpen, setStickerOpen] = useState(defaultStickerTray && !!member);
  const [atBottom, setAtBottom] = useState(true);
  const [placement, setPlacement] = useState(loadPlacement);   // null = CSS 기본 자리(오른쪽 아래)
  const [opacity, setOpacity] = useState(loadOpacity);
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef(null);                                  // { id, x, y, right, bottom, moved }
  const suppressClickRef = useRef(false);
  const dividerReadyRef = useRef(false);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const stickToBottomRef = useRef(true);
  const mountedRef = useRef(false);
  const pendingRequestsRef = useRef(new Set());
  const historyPositionRef = useRef(null);
  const syncedSeenRef = useRef(-1);
  const serverSeenRef = useRef(-1);
  const syncingSeenRef = useRef(false);

  useLayoutEffect(() => {
    if (!mobilePlacement) return undefined;
    // A desktop drag may be in progress when a window enters the mobile layout.
    const drag = dragRef.current;
    const fab = rootRef.current?.querySelector(".chat-fab");
    if (drag && fab?.hasPointerCapture?.(drag.id)) fab.releasePointerCapture(drag.id);
    dragRef.current = null;
    suppressClickRef.current = false;
    setDragging(false);
    const root = rootRef.current;
    const viewport = window.visualViewport;
    const fitVisibleViewport = () => {
      if (!root) return;
      const height = viewport?.height || window.innerHeight;
      const obscuredBottom = Math.max(0, window.innerHeight - height - (viewport?.offsetTop || 0));
      root.style.setProperty("--chat-visible-height", `${height}px`);
      root.style.setProperty("--chat-keyboard-offset", `${obscuredBottom}px`);
    };
    fitVisibleViewport();
    viewport?.addEventListener("resize", fitVisibleViewport);
    viewport?.addEventListener("scroll", fitVisibleViewport);
    window.addEventListener("resize", fitVisibleViewport);
    return () => {
      viewport?.removeEventListener("resize", fitVisibleViewport);
      viewport?.removeEventListener("scroll", fitVisibleViewport);
      window.removeEventListener("resize", fitVisibleViewport);
      root?.style.removeProperty("--chat-visible-height");
      root?.style.removeProperty("--chat-keyboard-offset");
    };
  }, [mobilePlacement]);

  const isCurrent = useCallback(() => mountedRef.current && chatScope(getToken() ? getAuthUser()?.id : null) === scope, [scope]);
  useEffect(() => {
    mountedRef.current = true;
    const pending = pendingRequestsRef.current;
    return () => {
      mountedRef.current = false;
      pending.forEach((controller) => controller.abort());
    };
  }, []);

  const load = useCallback(async (signal) => {
    const data = await api.chatList({ signal, seenId: getChatFeed(scope).seenId });
    if (signal.aborted || !isCurrent()) return;
    serverSeenRef.current = Math.max(serverSeenRef.current, Number(data.server_seen_id) || 0);
    receiveChat(scope, data);
  }, [isCurrent, scope]);
  const refresh = useAdaptivePolling(load, {
    intervalMs: POLL_MS, maxIntervalMs: 60_000, pollKey: scope,
    onError: (reason) => { if (isCurrent() && reason?.name !== "AbortError") setChatLoadError(scope, reason); },
  });

  // Cross-tab reads and overlapping history queries can make a count snapshot
  // obsolete. Request the current cursor immediately when its missing range
  // cannot be reconstructed from the messages already loaded in this tab.
  useEffect(() => {
    if (loaded && (feed.countNeedsRefresh || (seenId > feed.unreadSeenId && seenId < feed.latestId))) refresh();
  }, [feed.countNeedsRefresh, feed.latestId, feed.unreadSeenId, loaded, refresh, seenId]);

  // A local cursor can be ahead of another device. Keep failed writes pending
  // until a PUT or persisted server acknowledgement succeeds. Polls retry them.
  useEffect(() => {
    if (!member || !loaded || seenId == null) return;
    if (seenId <= Math.max(syncedSeenRef.current, serverSeenRef.current)) {
      setReadError("");
      return;
    }
    if (syncingSeenRef.current) return;
    syncingSeenRef.current = true;
    const controller = new AbortController();
    pendingRequestsRef.current.add(controller);
    api.chatRead(seenId, { signal: controller.signal }).then((data) => {
      if (controller.signal.aborted || !isCurrent()) return;
      syncedSeenRef.current = Math.max(syncedSeenRef.current, Number(data.seen_id) || seenId);
      markChatSeen(scope, data.seen_id ?? seenId);
      setReadError("");
    }).catch(() => {
      if (!controller.signal.aborted && isCurrent()) setReadError("읽음 상태를 저장하지 못했어요.");
    }).finally(() => {
      pendingRequestsRef.current.delete(controller);
      syncingSeenRef.current = false;
    });
  }, [feed.responseRevision, isCurrent, loaded, member, readRetry, scope, seenId]);

  const unseen = chatUnseenCount(feed, member?.id);
  const badge = badgeLabel(unseen);

  const markVisibleBottom = useCallback(() => {
    const element = listRef.current;
    if (!open || !loaded || !element || document.hidden || !isCurrent()) return;
    const rect = element.getBoundingClientRect();
    const bottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 3;
    const visible = element.clientHeight > 0 && rect.top >= 0 && rect.bottom <= window.innerHeight + 2;
    if (bottom && visible) markChatSeen(scope, visibleChatReadId(getChatFeed(scope)));
  }, [isCurrent, loaded, open, scope]);

  useLayoutEffect(() => {
    if (!open || !loaded) return undefined;
    if (!dividerReadyRef.current) {
      dividerReadyRef.current = true;
      setDividerId(firstUnseenId(items, seenId, member?.id));
    }
    const element = listRef.current;
    if (element) {
      const position = historyPositionRef.current;
      if (position) {
        element.scrollTop = position.top + element.scrollHeight - position.height;
        historyPositionRef.current = null;
      } else if (stickToBottomRef.current) {
        element.scrollTop = element.scrollHeight;
      }
      setAtBottom(element.scrollHeight - element.scrollTop - element.clientHeight <= 3);
    }
    const frame = window.requestAnimationFrame(markVisibleBottom);
    return () => window.cancelAnimationFrame(frame);
  }, [items, loaded, markVisibleBottom, member?.id, open, seenId]);

  useEffect(() => {
    const onVisible = () => {
      if (!document.hidden) window.requestAnimationFrame(markVisibleBottom);
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("resize", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("resize", onVisible);
    };
  }, [markVisibleBottom]);

  const close = useCallback(() => {
    dividerReadyRef.current = false;
    stickToBottomRef.current = true;
    setDividerId(null);
    setStickerOpen(false);
    setOpen(false);
  }, []);

  useEffect(() => {
    if (!open) return undefined;
    const frame = window.requestAnimationFrame(() => inputRef.current?.focus({ preventScroll: true }));
    const onKeyDown = (event) => {
      if (event.key !== "Escape") return;
      if (stickerOpen) setStickerOpen(false);
      else close();
    };
    const onPointerDown = (event) => {
      if (rootRef.current && !rootRef.current.contains(event.target)) close();
    };
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("pointerdown", onPointerDown);
    return () => {
      window.cancelAnimationFrame(frame);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("pointerdown", onPointerDown);
    };
  }, [close, open, stickerOpen]);

  // 끌어서 옮기기 — 버튼을 잡고 6px 넘게 움직이면 드래그, 아니면 클릭. 자리는 이 브라우저에 남는다.
  function onFabPointerDown(event) {
    if (isMobilePlacement()) return;
    if (event.button != null && event.button !== 0) return;
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    dragRef.current = {
      id: event.pointerId, x: event.clientX, y: event.clientY, moved: false,
      right: window.innerWidth - rect.right, bottom: window.innerHeight - rect.bottom,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }
  function onFabPointerMove(event) {
    if (isMobilePlacement()) return;
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    if (!drag.moved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
    if (!drag.moved) { drag.moved = true; setDragging(true); }
    setPlacement(clampPlacement({ right: drag.right - dx, bottom: drag.bottom - dy }, rootRef.current));
  }
  function onFabPointerEnd(event) {
    if (isMobilePlacement()) return;
    const drag = dragRef.current;
    if (!drag || drag.id !== event.pointerId) return;
    dragRef.current = null;
    if (drag.moved) {
      suppressClickRef.current = true;             // 놓는 순간의 click 은 열고 닫기가 아니다
      setDragging(false);
      setPlacement((current) => { if (current) writeStorage(PLACEMENT_KEY, JSON.stringify(current)); return current; });
    }
  }
  function onFabClick() {
    if (suppressClickRef.current) { suppressClickRef.current = false; return; }
    toggle();
  }
  function changeOpacity(event) {
    const next = Math.min(1, Math.max(0.25, Number(event.target.value) / 100));
    setOpacity(next);
    writeStorage(OPACITY_KEY, String(next));
  }
  // 창 크기가 바뀌거나 시트가 열리면 옮겨 둔 자리가 화면 밖으로 나가지 않게 다시 자른다.
  useLayoutEffect(() => {
    if (mobilePlacement || !placement) return undefined;
    const fit = () => {
      // The resize event can arrive before the matchMedia subscription renders.
      if (isMobilePlacement()) return;
      setPlacement((current) => {
        const next = clampPlacement(current, rootRef.current);
        return next && current && next.right === current.right && next.bottom === current.bottom ? current : next;
      });
    };
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  }, [open, placement != null, mobilePlacement]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggle() {
    if (open) close();
    else {
      dividerReadyRef.current = loaded;
      setDividerId(loaded ? firstUnseenId(items, seenId, member?.id) : null);
      stickToBottomRef.current = true;
      setOpen(true);
    }
  }

  function jumpToLatest() {
    stickToBottomRef.current = true;
    if (listRef.current) listRef.current.scrollTop = listRef.current.scrollHeight;
    setAtBottom(true);
    window.requestAnimationFrame(markVisibleBottom);
  }

  async function loadOlder() {
    if (loadingOlder || !feed.hasMore || !feed.oldestId) return;
    const controller = new AbortController();
    pendingRequestsRef.current.add(controller);
    setLoadingOlder(true);
    const beforeId = feed.oldestId;
    try {
      const data = await api.chatList({ signal: controller.signal, beforeId, seenId: getChatFeed(scope).seenId });
      if (controller.signal.aborted || !isCurrent() || Number(getChatFeed(scope).oldestId) !== Number(beforeId)) return;
      const element = listRef.current;
      if (element) historyPositionRef.current = { top: element.scrollTop, height: element.scrollHeight };
      stickToBottomRef.current = false;
      receiveChat(scope, data, { older: true, beforeId });
    } catch (reason) {
      if (!controller.signal.aborted && isCurrent()) setChatLoadError(scope, reason);
    } finally {
      pendingRequestsRef.current.delete(controller);
      if (isCurrent()) setLoadingOlder(false);
    }
  }

  async function post(body) {
    if (!member || busy || !body.trim() || !isCurrent()) return false;
    setError("");
    setBusy(true);
    const controller = new AbortController();
    pendingRequestsRef.current.add(controller);
    try {
      const result = await api.chatPost(body.trim(), { signal: controller.signal });
      if (controller.signal.aborted || !isCurrent()) return false;
      stickToBottomRef.current = true;
      if (result?.message) receiveChatPost(scope, result.message);
      refresh();
      return true;
    } catch (reason) {
      if (!controller.signal.aborted && isCurrent()) setError(String(reason.message || reason));
      return false;
    } finally {
      pendingRequestsRef.current.delete(controller);
      if (isCurrent()) {
        setBusy(false);
        inputRef.current?.focus({ preventScroll: true });
      }
    }
  }

  async function send(event) {
    event.preventDefault();
    if (await post(text)) setText("");
  }

  async function sendSticker(id) {
    setStickerOpen(false);
    await post(stickerText(id));
  }

  return (
    <div className={`chat-float${dragging ? " is-dragging" : ""}${mobilePlacement ? " is-mobile-fixed" : ""}`} ref={rootRef} style={!mobilePlacement && placement ? { right: placement.right, bottom: placement.bottom } : undefined}>
      {open ? (
        <section id={panelId} className="chat-sheet" role="dialog" aria-label="리더보드 채팅" style={opacity < 1 ? { "--chat-sheet-opacity": opacity } : undefined}>
          <header className="chat-head">
            <div className="chat-head-title"><h3>리더보드 채팅</h3><p>KST <span className="num">{kstClock()}</span> · 오늘의 대화</p></div>
            <div className="chat-head-tools">
              <label className="chat-opacity" title={`투명도 ${Math.round((1 - opacity) * 100)}%`}>
                <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12Z" /><circle cx="12" cy="12" r="3" /></svg>
                <input type="range" min="25" max="100" step="5" value={Math.round(opacity * 100)} onChange={changeOpacity} aria-label="채팅창 불투명도" aria-valuetext={`${Math.round(opacity * 100)}%`} />
              </label>
              <button type="button" className="chat-close" onClick={close} aria-label="채팅 닫기"><svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="m4 4 8 8M12 4l-8 8" /></svg></button>
            </div>
          </header>
          {loadError || readError ? (
            <div className="chat-status is-error" role="alert">
              <span>{loadError ? "채팅을 불러오지 못했어요. 연결을 확인해 주세요." : readError}</span>
              <button type="button" onClick={() => { setReadRetry((value) => value + 1); refresh(); }}>다시 시도</button>
            </div>
          ) : null}
          <div ref={listRef} role="log" aria-label="리더보드 채팅 메시지" aria-busy={!loaded && !loadError} className="chat-log" onScroll={(event) => {
            const element = event.currentTarget;
            const bottom = element.scrollHeight - element.scrollTop - element.clientHeight <= 3;
            stickToBottomRef.current = bottom;
            setAtBottom(bottom);
            markVisibleBottom();
          }}>
            {loaded && feed.hasMore ? <button type="button" className="chat-history" onClick={loadOlder} disabled={loadingOlder}>{loadingOlder ? "불러오는 중…" : "이전 메시지 더 보기"}</button> : null}
            {!loaded ? (
              loadError ? <p className="chat-helper">대화를 불러오면 여기에 표시됩니다.</p> : <div className="chat-skeleton" aria-hidden="true"><i /><i /><i /></div>
            ) : items.length === 0 ? (
              <div className="chat-empty"><img src={EMPTY_FACE} alt="" width="56" height="56" draggable="false" /><strong>아직 조용해요.</strong><span>오늘 첫 채팅을 남겨봐요.</span></div>
            ) : items.map((message, index) => {
              const previous = index > 0 ? items[index - 1] : null;
              const showDivider = dividerId != null && message.id === dividerId;
              const continued = !showDivider && sameAuthor(previous, message);
              const mine = isOwnMessage(message, member?.id);
              const legacy = message.user_id == null;
              const sticker = stickerFromText(message.text);
              return (
                <Fragment key={message.id}>
                  {showDivider ? <div className="chat-divider" role="separator" aria-label="여기부터 새 메시지"><span>새 메시지</span></div> : null}
                  <article className={`chat-row${mine ? " is-mine" : ""}${continued ? " is-continued" : ""}`} aria-label={`${message.username}${legacy ? ", 이전 익명 메시지" : ""}, ${message.created_kst}`}>
                    {!mine ? (continued ? <span className="chat-avatar" aria-hidden="true" /> : <AuthorAvatar userId={message.user_id} src={message.avatar_url} name={message.username} size={32} className="chat-avatar" />) : null}
                    <div className="chat-row-body">
                      {!mine && !continued ? <span className="chat-row-name">{message.username}{legacy ? <small className="chat-legacy">이전 익명</small> : null}</span> : null}
                      <div className="chat-bubble-line">
                        {sticker
                          ? <p className="chat-bubble is-sticker"><img src={sticker.src} alt={`${sticker.label} 스티커`} width="92" height="92" draggable="false" decoding="async" /></p>
                          : message.macros?.length
                            ? <div className="chat-bubble is-macro">{splitMacroText(message.text, message.macros).map((part, partIndex) => (
                                part.type === "text"
                                  ? <p key={`t-${partIndex}`} className="chat-bubble-text">{part.text}</p>
                                  : <MacroCard key={`m-${part.card.entry_id}-${partIndex}`} card={part.card} onClose={close} />
                              ))}</div>
                            : <p className="chat-bubble">{message.text}</p>}
                        <time className="num">{message.created_kst}</time>
                      </div>
                    </div>
                  </article>
                </Fragment>
              );
            })}
          </div>
          {!atBottom && loaded ? <button type="button" className="chat-jump" onClick={jumpToLatest}>{unseen ? `새 메시지 ${unseen}개 · 아래로` : "최신 메시지로 이동"}</button> : null}
          {member && stickerOpen ? (
            <div className="chat-sticker-tray" role="group" aria-label="스티커 고르기">
              {STICKERS.map((sticker) => <button key={sticker.id} type="button" className="chat-sticker-tile" onClick={() => sendSticker(sticker.id)} disabled={busy} aria-label={`${sticker.label} 스티커 보내기`}><img src={sticker.src} alt="" width="56" height="56" draggable="false" decoding="async" /><span>{sticker.label}</span></button>)}
            </div>
          ) : null}
          {/* 붙여넣은 매크로 링크 — 보내면 카드로 바뀐다는 걸 미리 알려 준다. */}
          {member && macroIdsInText(text).length ? (
            <p className="chat-macro-hint">매크로 <b className="num">{macroIdsInText(text).length}</b>개를 언급했어요. 보내면 카드로 보여요.</p>
          ) : null}
          {member ? (
            <form onSubmit={send} className="chat-composer">
              <span className="chat-name-chip chat-member-name" aria-label={`로그인 회원 ${member.username}`}><UserAvatar src={member.avatar_url} name={member.username} size={24} /><span className="chat-member-label">{member.username}</span></span>
              <button type="button" className={`chat-sticker-btn${stickerOpen ? " is-on" : ""}`} onClick={() => setStickerOpen((tray) => !tray)} aria-expanded={stickerOpen} aria-label={stickerOpen ? "스티커 닫기" : "스티커 열기"} title="스티커" disabled={busy}><img src={STICKERS[0].src} alt="" width="22" height="22" draggable="false" decoding="async" /></button>
              <input ref={inputRef} value={text} aria-label="채팅 메시지" aria-invalid={error ? true : undefined} onChange={(event) => setText(event.target.value)} maxLength={300} placeholder="메시지" className="chat-field" disabled={busy} />
              <button type="submit" className="chat-send" disabled={busy || !text.trim()} aria-label={busy ? "보내는 중" : "전송"}>{busy ? <span className="chat-spinner" aria-hidden="true" /> : <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M8 13V3M3.5 7.5 8 3l4.5 4.5" /></svg>}</button>
            </form>
          ) : <div className="chat-login-prompt"><span>회원으로 로그인하고 대화에 참여해 보세요.</span><a href="/login?next=%2Fleaderboard">로그인</a></div>}
          {error ? <p className="chat-helper is-error" role="alert">{error}</p> : <span className="chat-composer-gap" aria-hidden="true" />}
        </section>
      ) : null}
      <button type="button" className={`chat-fab${open ? " is-open" : ""}${unseen ? " has-news" : ""}${dragging ? " is-dragging" : ""}`} onClick={onFabClick} onPointerDown={mobilePlacement ? undefined : onFabPointerDown} onPointerMove={mobilePlacement ? undefined : onFabPointerMove} onPointerUp={mobilePlacement ? undefined : onFabPointerEnd} onPointerCancel={mobilePlacement ? undefined : onFabPointerEnd} title={mobilePlacement ? undefined : "끌어서 옮길 수 있어요"} aria-expanded={open} aria-controls={open ? panelId : undefined} aria-label={open ? "채팅 닫기" : badge ? `채팅 열기, 새 메시지 ${unseen}개` : "채팅 열기"}>
        <img src={FAB_ICON} alt="" width="44" height="44" draggable="false" decoding="async" /><span className="chat-fab-label" aria-hidden="true">Chat</span>{badge ? <span className="chat-fab-badge num" aria-hidden="true">{badge}</span> : null}
      </button>
    </div>
  );
}
